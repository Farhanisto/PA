"""Background tasks: small JSON-serializable payloads (IDs + data only)."""

import json
import logging
import os
import time
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


class EchoFakeModel:
    """Deterministic fallback for tests / when no provider key is configured."""

    def __init__(self, prefix: str = "Echo response"):
        self.prefix = prefix

    def invoke(self, prompt: Any) -> Any:
        text = prompt if isinstance(prompt, str) else str(prompt)

        class Msg:
            def __init__(self, content: str):
                self.content = content

        return Msg(f"{self.prefix}: {text[:500]}")


def _resolve_task_model():
    if _model_override is not None:
        return _model_override() if callable(_model_override) else _model_override
    if os.getenv("PA_FAKE_MODEL") == "1" or os.getenv("PRIVATEOS_FAKE_MODEL") == "1":
        return EchoFakeModel()
    from pa.models.nebius import create_nebius_model

    return create_nebius_model()


def is_transient_error(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "timeout" in name or "connection" in name or "retry" in name:
        return True
    return any(m in msg for m in TRANSIENT_MARKERS)


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
        initial_state = {
            "user_id": job.user_id,
            "request": job.request,
            "memory_snippets": snippets,
        }

        started = time.monotonic()
        try:
            final_state = _run_graph(job_id, job.thread_id, initial_state)
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
            if "model_error" in err and is_transient_error(Exception(err)):
                try:
                    raise self.retry(exc=Exception(err), countdown=2**self.request.retries)
                except Retry:
                    raise
            repositories.update_job(db, job, status="failed", error_code=err[:128], finished=True)
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
        repositories.update_job(db, job, status="completed", result=result_text, finished=True)
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
        # Re-check policy/permissions immediately before side effect.
        from pa.security.policy import is_action_allowed, requires_approval
        from pa.tools.registry import is_allowed

        try:
            payload = json.loads(approval.payload or "{}")
        except json.JSONDecodeError:
            payload = {}
        allowed = is_action_allowed(tool_name="approved_echo", user_id=job.user_id, args={})
        if approval.user_id != job.user_id or not allowed:
            repositories.update_job(
                db, job, status="failed", error_code="policy_denied", finished=True
            )
            _job_log(db, job, "policy.denied", {"approval_id": approval.id})
            return {"ok": False, "error": "policy_denied"}

        repositories.update_job(db, job, status="running")
        _job_log(db, job, "job.resume_started", {"approval_id": approval.id})

        # Idempotent side effect: only execute once per approval.
        # If the stored proposal names an allowlisted integration tool,
        # execute it with its stored args; otherwise fall back to the echo stub.
        from pa.tools.registry import invoke_tool

        tool_name = payload.get("tool") if isinstance(payload, dict) else None
        tool_args = payload.get("args") if isinstance(payload, dict) else None
        outcome = ""
        try:
            if (
                isinstance(tool_name, str)
                and is_allowed(tool_name)
                and requires_approval(tool_name)
                and isinstance(tool_args, dict)
            ):
                if not is_action_allowed(tool_name=tool_name, user_id=job.user_id, args=tool_args):
                    raise ValueError(f"stored args rejected for {tool_name}")
                outcome = invoke_tool(tool_name, tool_args)
            else:
                outcome = invoke_tool(
                    "approved_echo",
                    {
                        "message": str(
                            payload.get("summary") or payload.get("action") or approval.action
                        )
                    },
                )
        except Exception as exc:
            repositories.update_job(
                db, job, status="failed", error_code="tool_error", finished=True
            )
            return {"ok": False, "error": str(exc)[:500]}

        try:
            final_state = _run_graph(
                job.id, job.thread_id, {}, resume_value={"decision": "approved"}
            )
        except Exception as exc:
            if is_transient_error(exc):
                try:
                    raise self.retry(exc=exc, countdown=2**self.request.retries)
                except Retry:
                    raise
            repositories.update_job(
                db, job, status="failed", error_code="resume_failed", finished=True
            )
            return {"ok": False, "error": str(exc)[:500]}

        prev_result = job.result or ""
        response = (
            (final_state.get("response") or prev_result)
            if isinstance(final_state, dict)
            else prev_result
        )
        combined = (response + f"\n\n[Approved action outcome: {outcome}]").strip()
        repositories.update_job(db, job, status="completed", result=combined, finished=True)
        _job_log(db, job, "job.completed_after_approval", {"approval_id": approval.id})
        return {"ok": True, "status": "completed"}
    finally:
        db.close()


@celery_app.task(bind=True, name="pa.run_daily_briefing", max_retries=3)
def run_daily_briefing(self, user_id: str, scheduled_for: str = "") -> dict:
    """Seeded briefing workflow: no third-party integrations in MVP."""
    from pa.observability import log_event

    init_db()
    factory = get_session_factory()
    db = factory()
    try:
        try:
            snippets = context_snippets(db, user_id=user_id)
        except Exception:
            snippets = []
        try:
            from pa.memory.service import list_memories as _list

            seeded_tasks = [m.content for m in _list(db, user_id=user_id, category="task")[:10]]
            seeded_notes = [m.content for m in _list(db, user_id=user_id, category="note")[:10]]
        except Exception:
            seeded_tasks, seeded_notes = [], []
        from pa.tools.briefing import build_briefing

        notes = seeded_notes + ([f"Scheduled for {scheduled_for}"] if scheduled_for else [])
        draft = build_briefing(tasks=seeded_tasks, notes=notes, preferences=snippets)
        log_event(
            logger,
            logging.INFO,
            "briefing draft built",
            user_id=user_id,
            extra={"tasks": len(seeded_tasks), "notes": len(notes)},
        )
        # Try model polish, fall back to draft on provider failure.
        try:
            model = _resolve_task_model()
            t0 = time.monotonic()
            prompt = f"Polish this briefing concisely:\n\n{draft}"
            res = model.invoke(prompt) if hasattr(model, "invoke") else model(prompt)
            polished = getattr(res, "content", res)
            final = str(polished).strip() or draft
            log_event(
                logger,
                logging.INFO,
                "briefing polished",
                user_id=user_id,
                extra={"latency_ms": int((time.monotonic() - t0) * 1000)},
            )
        except Exception as exc:
            if is_transient_error(exc):
                try:
                    raise self.retry(exc=exc, countdown=2**self.request.retries)
                except Retry:
                    raise
            log_event(logger, logging.WARNING, "briefing polish fallback", user_id=user_id)
            final = draft
        job = repositories.create_job(
            db, user_id=user_id, request=f"Daily briefing {scheduled_for}".strip()
        )
        repositories.update_job(db, job, status="completed", result=final, finished=True)
        _job_log(db, job, "briefing.completed", {"scheduled_for": scheduled_for})
        log_event(logger, logging.INFO, "briefing completed", job_id=job.id, user_id=user_id)
        return {"ok": True, "job_id": job.id, "result": final}
    finally:
        db.close()
