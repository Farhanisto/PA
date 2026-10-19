"""Background tasks: small JSON-serializable payloads (IDs + data only)."""

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

from celery.exceptions import Retry

from pa.db import repositories
from pa.db.database import get_session_factory, init_db
from pa.graph.builder import build_graph, get_thread_config
from pa.graph.checkpointer import get_checkpoint_db_path, get_checkpointer
from pa.memory.service import context_snippets
from pa.queue.celery_app import celery_app
from pa.security import audit as audit_mod

logger = logging.getLogger(__name__)

# Test hook: tests may set this to a fake model instance or factory.
_model_override: Any = None

TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "connection",
    "temporarily",
    "service unavailable",
    "502",
    "503",
    "504",
    "rate limit",
    "overloaded",
)


def set_model_override(model: Any) -> None:
    global _model_override
    _model_override = model


def clear_model_override() -> None:
    global _model_override
    _model_override = None


def _resolve_task_model():
    """Resolve the chat model for this run.

    Production path is the configured provider (Groq/Nebius). Tests inject a
    deterministic double via ``set_model_override``.
    """
    if _model_override is not None:
        return _model_override() if callable(_model_override) else _model_override
    from pa.models.factory import create_model

    return create_model()


def is_transient_error(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "timeout" in name or "connection" in name or "retry" in name:
        return True
    if any(m in msg for m in TRANSIENT_MARKERS):
        return True
    try:
        from pa.models.errors import is_transient

        return is_transient(exc)
    except Exception:
        return False


def _retry_countdown(exc: Exception, retries: int) -> int:
    """Seconds to wait before retrying.

    A token-budget failure (413) or a rate limit (429) is a *per-minute*
    budget: retrying in two seconds just fails again. Those wait for the
    window to refill; anything else backs off quickly.
    """
    try:
        from pa.models.errors import status_of

        status = status_of(exc)
    except Exception:
        status = None
    if status in (413, 429):
        return 30 * (retries + 1)
    return 2**retries


def _job_log(db, job, event: str, details: dict | None = None) -> None:
    try:
        audit_mod.record(
            db,
            event_type=event,
            user_id=getattr(job, "user_id", None),
            job_id=getattr(job, "job_id", None) or getattr(job, "id", None),
            details=details or {},
        )
    except Exception:
        logger.exception("audit record failed")


def _extract_interrupt_proposal(exc: BaseException) -> dict | None:
    """Best-effort extraction of interrupt payload across LangGraph versions."""
    # langgraph.errors.GraphInterrupt carries .args[0] = list of Interrupt objects
    try:
        args = getattr(exc, "args", ())
        if args:
            first = args[0]
            items = first if isinstance(first, list) else [first]
            for item in items:
                val = getattr(item, "value", item)
                if isinstance(val, dict):
                    if "proposed_action" in val:
                        return val.get("proposed_action") or val
                    if val.get("type") == "approval_required":
                        return val.get("proposed_action") or val
                    return val
    except Exception:
        pass
    return None


def _run_graph(
    job_id: str, thread_id: str, initial_state: dict, *, resume_value: Any = None
) -> dict:
    """Invoke (or resume) the graph with a persistent checkpointer.

    Returns the final state dict. Raises GraphInterrupt (or returns state
    with __interrupt__) when approval is required.
    """
    from langgraph.types import Command

    model = _resolve_task_model()
    db_path = get_checkpoint_db_path()
    with get_checkpointer(db_path) as checkpointer:
        graph = build_graph(checkpointer=checkpointer)
        if resume_value is None:
            config = get_thread_config(thread_id, model=model)
            return graph.invoke(initial_state, config=config)  # type: ignore[arg-type]
        config = get_thread_config(thread_id, model=model)
        cmd = Command(resume=resume_value)
        return graph.invoke(cmd, config=config)  # type: ignore[arg-type]


def _conversation_history(db, job, limit: int = 10) -> list[dict]:
    """The earlier turns of this conversation, oldest first.

    A short answer ("tickets") is meaningless without the question it
    answers, so the exchange is carried forward. Only the caller's own
    turns are included.
    """
    try:
        prior = repositories.list_jobs_for_conversation(
            db, user_id=job.user_id, conversation_id=job.conversation_id, limit=limit
        )
    except Exception:
        return []
    history: list[dict] = []
    for turn in prior:
        if turn.id == job.id or not turn.request:
            continue
        history.append({"role": "user", "content": turn.request})
        if turn.result:
            history.append({"role": "assistant", "content": turn.result[:2000]})
    return history


def _friendly_error_code(err: str) -> str:
    """A short, actionable error for the user; the full detail goes to the audit log.

    "model_error: APIStatusError: status=413: Error code: 413 - {...}" tells the
    user nothing they can act on.
    """
    if "model_error" in err:
        try:
            from pa.models.errors import FRIENDLY, status_of

            status = status_of(Exception(err))
            if status in FRIENDLY:
                return FRIENDLY[status]
        except Exception:
            pass
    return err[:128]


@celery_app.task(bind=True, name="pa.run_assistant_job", max_retries=3)
def run_assistant_job(self, job_id: str) -> dict:
    from pa.observability import log_event

    init_db()
    factory = get_session_factory()
    db = factory()
    try:
        job = repositories.get_job(db, job_id)
        if job is None:
            logger.error("job not found", extra={"job_id": job_id})
            return {"ok": False, "error": "not_found"}
        if job.status in ("completed", "failed", "cancelled"):
            return {"ok": True, "status": job.status, "duplicate": True}
        if job.status == "running":
            # Guarded: possible duplicate delivery; do not run twice.
            return {"ok": True, "status": job.status, "duplicate": True}

        repositories.update_job(db, job, status="running", started=True)
        _job_log(db, job, "job.started", {"thread_id": job.thread_id})
        log_event(
            logger,
            logging.INFO,
            "job started",
            job_id=job.id,
            thread_id=job.thread_id,
            user_id=job.user_id,
        )

        # Load authorized context only (user-scoped memories).
        try:
            snippets = context_snippets(db, user_id=job.user_id)
        except Exception:
            snippets = []

        # Which apps this user has actually connected, and their tools.
        try:
            from pa.services.app_context import connected_apps_context

            app_ctx = connected_apps_context(job.user_id)
        except Exception as exc:
            log_event(
                logger, logging.WARNING, "app context unavailable",
                job_id=job.id, user_id=job.user_id,
                details={"error": str(exc)[:200]},
            )
            app_ctx = {"text": "", "apps": [], "tools": {}}

        initial_state = {
            "user_id": job.user_id,
            "request": job.request,
            "memory_snippets": snippets,
            "app_context": app_ctx["text"],
            "connected_apps": app_ctx["apps"],
            "history": _conversation_history(db, job),
            "focus": repositories.job_focus(job),
        }
        job_id, thread_id = job.id, job.thread_id
        # Release the read snapshot before the graph run: the checkpointer
        # may run DDL (Postgres CREATE INDEX CONCURRENTLY), which deadlocks
        # against snapshots held by concurrent transactions.
        db.commit()

        started = time.monotonic()
        try:
            final_state = _run_graph(job_id, thread_id, initial_state)
        except Exception as exc:
            # Detect LangGraph interrupt (approval pause) by class name to avoid
            # hard dependency on exact langgraph version.
            if type(exc).__name__ in ("GraphInterrupt", "Interrupt"):
                proposal = _extract_interrupt_proposal(exc) or {}
                log_event(
                    logger,
                    logging.INFO,
                    "job awaiting approval",
                    job_id=job.id,
                    thread_id=job.thread_id,
                    user_id=job.user_id,
                )
                return _handle_interrupt(db, job, proposal)
            if is_transient_error(exc):
                _job_log(db, job, "job.transient_error", {"error": str(exc)[:500]})
                log_event(
                    logger,
                    logging.WARNING,
                    "job transient error, retrying",
                    job_id=job.id,
                    thread_id=job.thread_id,
                    extra={"error": str(exc)[:200], "retry": self.request.retries},
                )
                try:
                    raise self.retry(exc=exc, countdown=2**self.request.retries, max_retries=3)
                except Retry:
                    raise
            repositories.update_job(
                db, job, status="failed", error_code="permanent_error", finished=True
            )
            _job_log(db, job, "job.failed", {"error": str(exc)[:500]})
            log_event(logger, logging.ERROR, "job failed", job_id=job.id, thread_id=job.thread_id)
            return {"ok": False, "error": str(exc)[:500]}

        latency_ms = int((time.monotonic() - started) * 1000)
        # Some versions return __interrupt__ in state instead of raising.
        interrupts = None
        if isinstance(final_state, dict):
            interrupts = final_state.get("__interrupt__")
        if interrupts:
            payload = interrupts[0] if isinstance(interrupts, list) else interrupts
            val = getattr(payload, "value", payload)
            proposal = (val.get("proposed_action") if isinstance(val, dict) else None) or {}
            return _handle_interrupt(db, job, proposal)

        if isinstance(final_state, dict) and final_state.get("error"):
            err = str(final_state["error"])
            # The records the run already fetched are still worth showing.
            artifacts = final_state.get("artifacts") or []
            if "model_error" in err and is_transient_error(Exception(err)):
                try:
                    raise self.retry(
                        exc=Exception(err),
                        countdown=_retry_countdown(Exception(err), self.request.retries),
                        max_retries=3,
                    )
                except Retry:
                    raise
            repositories.update_job(
                db,
                job,
                status="failed",
                error_code=_friendly_error_code(err),
                artifacts=artifacts,
                finished=True,
            )
            _job_log(db, job, "job.failed", {"error": err[:500], "latency_ms": latency_ms})
            return {"ok": False, "error": err}

        # Approval pause without exception (node set pending).
        if (
            isinstance(final_state, dict)
            and final_state.get("proposed_action")
            and final_state.get("approval_status") == "pending"
        ):
            return _handle_interrupt(db, job, final_state.get("proposed_action") or {})

        result_text = (final_state.get("response") or "") if isinstance(final_state, dict) else ""
        # Structured app records (HubSpot engagements, contacts, ...) the run
        # surfaced. The chat renders these as cards the user can act on.
        artifacts = (final_state.get("artifacts") or []) if isinstance(final_state, dict) else []
        repositories.update_job(
            db, job, status="completed", result=result_text, artifacts=artifacts, finished=True
        )
        _job_log(db, job, "job.completed", {"latency_ms": latency_ms})
        log_event(
            logger,
            logging.INFO,
            "job completed",
            job_id=job.id,
            thread_id=job.thread_id,
            user_id=job.user_id,
            extra={"latency_ms": latency_ms},
        )
        return {"ok": True, "status": "completed", "job_id": job.id}
    finally:
        db.close()


def _handle_interrupt(db, job, proposal: dict) -> dict:
    import json as _json

    action = (
        proposal.get("action") if isinstance(proposal, dict) else None
    ) or "user_requested_action"
    payload = proposal if isinstance(proposal, dict) else {"summary": str(proposal)}
    # Idempotency: reuse existing pending approval for this thread if present.
    existing = repositories.get_pending_approval_for_thread(db, job.thread_id)
    if existing is None:
        approval = repositories.create_approval(
            db,
            user_id=job.user_id,
            job_id=job.id,
            thread_id=job.thread_id,
            action=action,
            payload=payload,
        )
    else:
        approval = existing
    repositories.update_job(db, job, status="waiting_for_approval")
    _job_log(
        db,
        job,
        "job.waiting_for_approval",
        {"approval_id": approval.id, "action": action},
    )
    try:
        payload_str = approval.payload
        _json.loads(payload_str)
    except Exception:
        pass
    return {"ok": True, "status": "waiting_for_approval", "approval_id": approval.id}


def _execute_approved_tool(db, job, approval, payload: dict) -> dict:
    """Run the exact Composio tool the user approved.

    The payload was frozen when the approval was created, and it is re-validated
    against the caller's connected apps here so a tampered record cannot widen
    the blast radius.
    """
    from pa.services import tracing as _tracing
    from pa.services.tool_runner import ToolError, execute, tool_index, validate

    tool = payload.get("tool")
    if not isinstance(tool, str) or not tool:
        repositories.update_job(db, job, status="failed", error_code="bad_payload", finished=True)
        return {"ok": False, "error": "approval payload missing tool"}

    args = payload.get("args") or {}
    if not isinstance(args, dict):
        args = {}

    try:
        index = tool_index(job.user_id)
        checked = validate(index, tool, args)
        # Re-classify: if the tool is not a write it should never have been
        # gated, but refuse rather than silently widening what runs.
        if index[tool]["classification"] != "write":
            raise ValueError(f"{tool} is not a write tool and cannot be approved here")

        job_id = job.id
        db.commit()
        with _tracing.trace_tool(
            db,
            user_id=job.user_id,
            job_id=job_id,
            tool_name=tool,
            args=checked,
        ) as rec:
            result = execute(job.user_id, tool, checked)
            rec["output"] = {"result": result.get("result", "")[:2000]}
    except ToolError as exc:
        repositories.update_job(db, job, status="failed", error_code="tool_error", finished=True)
        _job_log(db, job, "tool.failed", {"tool": tool, "error": str(exc)[:300]})
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        repositories.update_job(db, job, status="failed", error_code="tool_error", finished=True)
        _job_log(db, job, "tool.failed", {"tool": tool, "error": str(exc)[:300]})
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}

    summary = result.get("result", "")[:2000]
    repositories.update_job(
        db, job, status="completed",
        result=f"Ran {tool} in {index[tool]['app']}.\n{summary}",
        finished=True,
    )
    _job_log(db, job, "tool.approved_and_run", {"tool": tool, "approval_id": approval.id})
    return {"ok": True, "status": "completed", "tool": tool}


@celery_app.task(bind=True, name="pa.resume_approved_workflow", max_retries=3)
def resume_approved_workflow(self, approval_id: str) -> dict:
    init_db()
    factory = get_session_factory()
    db = factory()
    try:
        approval = repositories.get_approval(db, approval_id)
        if approval is None:
            return {"ok": False, "error": "approval_not_found"}
        if approval.status != "approved":
            return {"ok": False, "error": f"approval not approved: {approval.status}"}
        job = repositories.get_job(db, approval.job_id)
        if job is None:
            return {"ok": False, "error": "job_not_found"}
        if job.status == "completed":
            return {"ok": True, "status": "completed", "duplicate": True}
        try:
            payload = json.loads(approval.payload or "{}")
        except json.JSONDecodeError:
            payload = {}
        allowed = approval.user_id == job.user_id
        if not allowed:
            repositories.update_job(
                db, job, status="failed", error_code="policy_denied", finished=True
            )
            _job_log(db, job, "policy.denied", {"approval_id": approval.id})
            return {"ok": False, "error": "policy_denied"}

        repositories.update_job(db, job, status="running")
        _job_log(db, job, "job.resume_started", {"approval_id": approval.id})

        # The only approved action is sending a reviewed email draft.
        # Ownership, permission, and payload integrity are re-checked here,
        # immediately before the send.
        try:
            from pa.auth import PERM_SEND_EMAIL, permission_granted
            from pa.services import email_service as email_svc
            from pa.services import tracing as _tracing

            if approval.action == "composio_tool":
                return _execute_approved_tool(db, job, approval, payload)
            if approval.action != "email_send_draft":
                raise ValueError(f"unsupported approval action: {approval.action}")
            if not permission_granted(db, user_id=job.user_id, key=PERM_SEND_EMAIL):
                raise PermissionError("send permission denied")

            draft_id = payload.get("draft_id")
            if not isinstance(draft_id, str) or not draft_id:
                raise ValueError("approval payload missing draft_id")

            draft = email_svc.get_draft(db, user_id=job.user_id, draft_id=draft_id)
            if draft.to_addr != payload.get("to_addr"):
                raise ValueError("draft recipient changed since approval")

            job_id = job.id
            db.commit()
            with _tracing.trace_tool(
                db,
                user_id=job.user_id,
                job_id=job_id,
                tool_name="email_send_draft",
                args={"draft_id": draft_id, "to_addr": draft.to_addr},
            ) as rec:
                sent = email_svc.send_draft(db, user_id=job.user_id, draft_id=draft_id)
                rec["output"] = {"sent_message_id": sent.sent_message_id}

            result = (
                f"Approved email sent to {draft.to_addr}.\n"
                f"Subject: {draft.subject}\nProvider id: {sent.sent_message_id}"
            )
            repositories.update_job(db, job, status="completed", result=result, finished=True)
            _job_log(
                db,
                job,
                "email.sent_after_approval",
                {"approval_id": approval.id, "draft_id": draft_id},
            )
            return {"ok": True, "status": "completed", "action": "email_send_draft"}
        except Exception as exc:
            error_code = "permission_denied" if isinstance(exc, PermissionError) else "tool_error"
            repositories.update_job(db, job, status="failed", error_code=error_code, finished=True)
            _job_log(
                db,
                job,
                "email.send_failed",
                {"approval_id": approval.id, "error": f"{type(exc).__name__}: {exc}"[:500]},
            )
            return {"ok": False, "error": str(exc)[:500]}
    finally:
        db.close()


# --- Proactive assistance (requirement §6) ---


def _proactive_enabled(db, user_id: str) -> bool:
    """Proactive work is opt-in per user (deny-by-default)."""
    try:
        from pa.auth import PERM_PROACTIVE, permission_granted

        return permission_granted(db, user_id=user_id, key=PERM_PROACTIVE)
    except Exception:
        return False


@celery_app.task(bind=True, name="pa.run_inbox_briefing", max_retries=2)
def run_inbox_briefing(
    self, user_id: str, scheduled_for: str = "", job_id: str | None = None
) -> dict:
    """Build (never send) a proactive inbox briefing for one user."""
    from pa.observability import log_event
    from pa.services import proactive

    init_db()
    db = get_session_factory()
    try:
        db = db()
        if not _proactive_enabled(db, user_id):
            log_event(logger, logging.INFO, "proactive briefing skipped", user_id=user_id)
            return {"ok": True, "skipped": "proactive_disabled"}

        jid = job_id or f"briefing-{user_id[:8]}"
        started = time.monotonic()
        try:
            result = proactive.build_inbox_briefing(db, user_id=user_id, job_id=jid)
        except Exception as exc:
            if is_transient_error(exc):
                raise self.retry(exc=exc, countdown=2**self.request.retries)
            raise

        latency_ms = int((time.monotonic() - started) * 1000)
        if job_id:
            job = repositories.get_job(db, job_id)
            if job is not None and job.user_id == user_id:
                repositories.update_job(
                    db, job, status="completed", result=result["text"], finished=True
                )
        _job_log(db, job, "email.briefing_ready", {"counts": result["counts"]} if job else {})
        log_event(
            logger,
            logging.INFO,
            "inbox briefing built",
            user_id=user_id,
            extra={"latency_ms": latency_ms, "counts": result["counts"]},
        )
        return {"ok": True, "text": result["text"], "counts": result["counts"]}
    finally:
        try:
            db.close()
        except Exception:
            pass


@celery_app.task(name="pa.run_followup_reminders", max_retries=2)
def run_followup_reminders() -> dict:
    """Fan out follow-up reminders for every user with proactive enabled."""
    from pa.observability import log_event
    from pa.services import proactive

    init_db()
    session = get_session_factory()()
    try:
        users = repositories.list_active_user_ids(session)
    finally:
        session.close()

    delivered = 0
    skipped = 0
    for user_id in users:
        session = get_session_factory()()
        try:
            if not _proactive_enabled(session, user_id):
                skipped += 1
                continue
            data = proactive.build_followup_reminders(session, user_id=user_id)
            if data["count"]:
                repositories.create_audit_event(
                    session,
                    event_type="email.followup_reminders",
                    user_id=user_id,
                    details={"count": data["count"], "text": data["text"][:2000]},
                )
                delivered += 1
            else:
                skipped += 1
        except Exception as exc:
            log_event(
                logger,
                logging.WARNING,
                "followup reminder failed",
                user_id=user_id,
                extra={"error": str(exc)[:200]},
            )
        finally:
            session.close()
    log_event(
        logger,
        logging.INFO,
        "followups complete",
        extra={"delivered": delivered, "skipped": skipped},
    )
    return {"ok": True, "delivered": delivered, "skipped": skipped}


@celery_app.task(name="pa.run_inbox_briefings", max_retries=2)
def run_inbox_briefings(scheduled_for: str = "") -> dict:
    """Beat fan-out: enqueue one inbox briefing per proactive-enabled user."""
    from pa.observability import log_event

    init_db()
    session = get_session_factory()()
    try:
        users = repositories.list_active_user_ids(session)
    finally:
        session.close()

    enqueued = []
    for user_id in users:
        try:
            run_inbox_briefing.delay(user_id, scheduled_for or "")
            enqueued.append(user_id)
        except Exception as exc:
            log_event(
                logger,
                logging.WARNING,
                "briefing fan-out failed",
                user_id=user_id,
                extra={"error": str(exc)[:200]},
            )
    return {"ok": True, "enqueued": enqueued}


def _is_due(schedule, *, which: str, now_local) -> bool:
    """True when a user's chosen local time falls inside this dispatcher window."""
    if which == "briefing":
        if not schedule.briefing_enabled:
            return False
        hour, minute = schedule.briefing_hour, schedule.briefing_minute
    else:
        if not schedule.followup_enabled:
            return False
        hour, minute = schedule.followup_hour, schedule.followup_minute
    target = hour * 60 + minute
    current = now_local.hour * 60 + now_local.minute
    # Beat runs every 15 minutes; fire once when we cross the target.
    return target <= current < target + 15


@celery_app.task(name="pa.dispatch_due_updates")
def dispatch_due_updates() -> dict:
    """Fan out one briefing/follow-up per user at *their* chosen local time.

    Celery's crontab is global, so it cannot express per-user times; this
    dispatcher runs frequently and fires each user once their local window
    is reached.
    """
    from pa.observability import log_event

    init_db()
    session = get_session_factory()()
    sent: dict[str, list[str]] = {"briefing": [], "followup": []}
    try:
        for schedule in repositories.list_schedules(session):
            try:
                from zoneinfo import ZoneInfo

                now_local = datetime.now(ZoneInfo(schedule.timezone))
            except Exception:
                now_local = datetime.now(UTC)

            if _is_due(schedule, which="briefing", now_local=now_local):
                try:
                    run_inbox_briefing.delay(schedule.user_id, now_local.isoformat())
                    sent["briefing"].append(schedule.user_id)
                except Exception as exc:
                    log_event(logger, "briefing_enqueue_failed", user_id=schedule.user_id,
                              details={"error": str(exc)[:200]})
            if _is_due(schedule, which="followup", now_local=now_local):
                try:
                    from pa.services import proactive

                    proactive.build_followup_reminders(session, user_id=schedule.user_id)
                    sent["followup"].append(schedule.user_id)
                except Exception as exc:
                    log_event(logger, "followup_failed", user_id=schedule.user_id,
                              details={"error": str(exc)[:200]})
    finally:
        session.close()
    return {"dispatched": sent}
