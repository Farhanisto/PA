"""Gradio frontend for PA — chat, jobs, approvals, memory, briefing, health.

Runs in-process against the same SQLite DB/services as the API, so:

    .venv/bin/python -m pa.frontend

launches the UI (default http://127.0.0.1:7860) with no Redis required.
For background execution start Redis + a Celery worker; otherwise the UI
falls back to eager in-process execution.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from pa.db import repositories
from pa.db.database import get_session_factory, init_db
from pa.observability import log_event

logger = logging.getLogger("pa.frontend")

DEFAULT_USER = "demo-user"
POLL_TIMEOUT_S = 120
POLL_INTERVAL_S = 1.0


def _db():
    return get_session_factory()()


def _clean_user(user_id: str | None) -> str:
    user_id = (user_id or "").strip() or DEFAULT_USER
    return user_id[:128]


# --- Service layer (no gradio dependency; unit-testable) ---


def submit_task(
    user_id: str, request_text: str, timeout_s: float = POLL_TIMEOUT_S
) -> dict[str, Any]:
    """Create a job, dispatch it, and wait for a terminal state."""
    from pa.security import audit as audit_mod

    user_id = _clean_user(user_id)
    request_text = (request_text or "").strip()
    if not request_text:
        return {"ok": False, "error": "Please enter a request."}
    init_db()
    db = _db()
    try:
        job = repositories.create_job(db, user_id=user_id, request=request_text[:10000])
        audit_mod.record(
            db,
            event_type="job.accepted",
            user_id=user_id,
            job_id=job.id,
            details={"thread_id": job.thread_id, "source": "gradio"},
        )
        job_id = job.id
    finally:
        db.close()

    # Dispatch to a worker; fall back to eager execution when no broker.
    dispatched = False
    try:
        from pa.queue.tasks import run_assistant_job

        run_assistant_job.delay(job_id)
        dispatched = True
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "broker dispatch failed, will run eagerly",
            job_id=job_id,
            user_id=user_id,
            extra={"error": str(exc)[:150]},
        )

    deadline = time.monotonic() + timeout_s
    db = _db()
    try:
        while time.monotonic() < deadline:
            job = repositories.get_job(db, job_id)
            if job is None:
                return {"ok": False, "error": "Job not found."}
            if job.status in ("completed", "failed", "cancelled"):
                break
            if job.status == "waiting_for_approval":
                break
            if not dispatched and job.status == "queued":
                # No worker available — run inline.
                db.close()
                try:
                    from pa.queue.tasks import run_assistant_job

                    run_assistant_job.apply(args=[job_id])
                except Exception as exc:
                    return {"ok": False, "error": f"Eager execution failed: {exc!s:.200}"}
                db = _db()
                dispatched = True
            time.sleep(POLL_INTERVAL_S)
            db.expire_all()
        job = repositories.get_job(db, job_id)
        return {
            "ok": True,
            "job_id": job.id,
            "thread_id": job.thread_id,
            "status": job.status,
            "result": job.result,
            "error_code": job.error_code,
        }
    finally:
        db.close()


def fetch_job(user_id: str, job_id: str) -> dict[str, Any]:
    user_id = _clean_user(user_id)
    job_id = (job_id or "").strip()
    if not job_id:
        return {"ok": False, "error": "Enter a job ID."}
    db = _db()
    try:
        job = repositories.get_job(db, job_id)
        if job is None:
            return {"ok": False, "error": "Job not found."}
        if job.user_id != user_id:
            return {"ok": False, "error": "Not authorized for this job."}
        return {
            "ok": True,
            "job_id": job.id,
            "thread_id": job.thread_id,
            "status": job.status,
            "request": job.request,
            "result": job.result,
            "error_code": job.error_code,
        }
    finally:
        db.close()


def recent_jobs(user_id: str, limit: int = 20) -> list[list[str]]:
    user_id = _clean_user(user_id)
    db = _db()
    try:
        jobs = repositories.list_jobs_for_user(db, user_id=user_id, limit=limit)
        return [[j.id[:8], j.status, (j.request or "")[:60], (j.result or "")[:80]] for j in jobs]
    finally:
        db.close()


def pending_approvals(user_id: str) -> tuple[list[list[str]], list[str]]:
    """Returns (table rows, approval-id choices)."""
    user_id = _clean_user(user_id)
    db = _db()
    try:
        aps = repositories.list_approvals_for_user(db, user_id=user_id, status="pending")
        rows = []
        for a in aps:
            try:
                payload = json.loads(a.payload or "{}")
                summary = str(payload.get("summary") or payload.get("request") or "")[:80]
            except json.JSONDecodeError:
                summary = ""
            rows.append([a.id, a.action, summary, a.created_at.isoformat() if a.created_at else ""])
        return rows, [a.id for a in aps]
    finally:
        db.close()


def decide_approval(user_id: str, approval_id: str, decision: str) -> dict[str, Any]:
    from pa.security import approvals as approvals_sec
    from pa.security import audit as audit_mod

    user_id = _clean_user(user_id)
    approval_id = (approval_id or "").strip()
    if not approval_id:
        return {"ok": False, "error": "Select a pending approval."}
    if decision not in ("approved", "rejected"):
        return {"ok": False, "error": "Unknown decision."}
    db = _db()
    try:
        ap = repositories.get_approval(db, approval_id)
        if ap is None:
            return {"ok": False, "error": "Approval not found."}
        if ap.user_id != user_id:
            return {"ok": False, "error": "Not authorized for this approval."}
        try:
            resolved = approvals_sec.decide(db, approval=ap, user_id=user_id, decision=decision)  # type: ignore[arg-type]
        except (PermissionError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        audit_mod.record(
            db,
            event_type=f"approval.{decision}",
            user_id=user_id,
            job_id=ap.job_id,
            details={"approval_id": ap.id},
        )
        if decision == "approved":
            try:
                from pa.queue.tasks import resume_approved_workflow

                try:
                    resume_approved_workflow.delay(ap.id)
                except Exception:
                    resume_approved_workflow.apply(args=[ap.id])
            except Exception as exc:
                return {
                    "ok": True,
                    "status": resolved.status,
                    "warning": f"Resume dispatch failed: {exc!s:.150}",
                }
        else:
            job = repositories.get_job(db, ap.job_id)
            if job is not None and job.status == "waiting_for_approval":
                repositories.update_job(
                    db, job, status="failed", error_code="approval_rejected", finished=True
                )
        return {"ok": True, "status": resolved.status}
    finally:
        db.close()


def list_memories(user_id: str) -> list[list[str]]:
    from pa.memory import service as mem_service

    user_id = _clean_user(user_id)
    db = _db()
    try:
        return [
            [m.id, m.category, (m.content or "")[:100]]
            for m in mem_service.list_memories(db, user_id=user_id)
        ]
    finally:
        db.close()


def add_memory(user_id: str, content: str, category: str) -> dict[str, Any]:
    from pa.memory import service as mem_service

    user_id = _clean_user(user_id)
    db = _db()
    try:
        try:
            mem = mem_service.save_memory(
                db,
                user_id=user_id,
                content=(content or "").strip(),
                category=(category or "general").strip() or "general",
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "id": mem.id}
    finally:
        db.close()


def delete_memory(user_id: str, memory_id: str) -> dict[str, Any]:
    from pa.memory import service as mem_service

    user_id = _clean_user(user_id)
    db = _db()
    try:
        try:
            mem_service.delete_memory(db, user_id=user_id, memory_id=(memory_id or "").strip())
        except KeyError:
            return {"ok": False, "error": "Memory not found."}
        except PermissionError:
            return {"ok": False, "error": "Not authorized."}
        return {"ok": True}
    finally:
        db.close()


def run_briefing(user_id: str, scheduled_for: str = "") -> dict[str, Any]:
    user_id = _clean_user(user_id)
    from pa.queue.tasks import run_daily_briefing

    try:
        try:
            result = run_daily_briefing.delay(user_id, scheduled_for or "")
            out = result.get(timeout=POLL_TIMEOUT_S)
        except Exception:
            out = run_daily_briefing.apply(args=[user_id, scheduled_for or ""]).get()
        if isinstance(out, dict) and out.get("ok"):
            return {"ok": True, "result": out.get("result", "")}
        return {"ok": False, "error": str(out)[:300]}
    except Exception as exc:
        return {"ok": False, "error": f"Briefing failed: {exc!s:.200}"}


def health_status() -> dict[str, Any]:
    from sqlalchemy import text

    out: dict[str, Any] = {}
    try:
        db = _db()
        try:
            db.execute(text("SELECT 1"))
            out["database"] = "ok"
        finally:
            db.close()
    except Exception as exc:
        out["database"] = f"error: {type(exc).__name__}"
    try:
        import redis

        client = redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"), socket_connect_timeout=2
        )
        client.ping()
        out["redis"] = "ok"
    except Exception as exc:
        out["redis"] = f"degraded: {type(exc).__name__}"
    return out


# --- Gradio UI ---


def integration_rows() -> list[list[str]]:
    from pa.integrations import manager

    init_db()
    return [
        [s["provider"], "yes" if s["configured"] else "no", s["mode"], s["description"]]
        for s in manager.connected_accounts()
    ]


def connect_provider(user_id: str, provider: str) -> str:
    from pa.integrations import manager

    user_id = _clean_user(user_id)
    provider = (provider or "").strip().lower()
    if not provider:
        return "Select a provider."
    db = _db()
    try:
        try:
            s = manager.connect(db, user_id=user_id, provider=provider)
        except ValueError as exc:
            return f"Error: {exc}"
        return f"Connected {provider} ({s.get('mode')} mode)."
    finally:
        db.close()


def disconnect_provider(user_id: str, provider: str) -> str:
    from pa.integrations import manager

    user_id = _clean_user(user_id)
    db = _db()
    try:
        try:
            manager.disconnect(db, user_id=user_id, provider=(provider or "").strip().lower())
        except ValueError as exc:
            return f"Error: {exc}"
        return f"Disconnected {provider}."
    finally:
        db.close()


def gmail_inbox_preview(limit: int = 5) -> list[list[str]]:
    from pa.integrations.gmail import GmailConnector

    try:
        n = max(1, min(int(limit), 25))
    except (TypeError, ValueError):
        n = 5
    return [[m["from"], m["subject"], m["snippet"][:80]] for m in GmailConnector().read_inbox(n)]


def zoom_meetings_preview() -> list[list[str]]:
    from pa.integrations.zoom import ZoomConnector

    return [
        [m["id"], m["topic"], m.get("start_time", ""), m.get("join_url", "")]
        for m in ZoomConnector().list_meetings()
    ]


def build_demo() -> Any:
    import gradio as gr

    init_db()

    with gr.Blocks(title="PA — Personal Assistant") as demo:
        gr.Markdown(
            "# PA — Personal Assistant\nSubmit tasks, track jobs, approve actions, manage memory, connect apps."
        )
        user_id = gr.Textbox(label="User ID", value=DEFAULT_USER, max_lines=1)

        with gr.Tab("Chat"):
            chatbot = gr.Chatbot(label="Conversation")
            msg = gr.Textbox(
                label="Request", placeholder="e.g. Prepare a concise morning briefing", lines=3
            )
            with gr.Row():
                send = gr.Button("Send", variant="primary")
                clear = gr.Button("Clear")
            chat_status = gr.Markdown()

            def _chat(user_msg: str, history: list, uid: str):
                if not (user_msg or "").strip():
                    return history, "", "Please enter a request."
                history = [*history, {"role": "user", "content": user_msg}]
                out = submit_task(uid, user_msg)
                if not out.get("ok"):
                    history.append({"role": "assistant", "content": f"Error: {out.get('error')}"})
                    return history, "", f"Error: {out.get('error')}"
                if out["status"] == "waiting_for_approval":
                    reply = (
                        f"Paused for approval (job `{out['job_id'][:8]}`). "
                        "See the Approvals tab to review and approve/reject."
                    )
                elif out["status"] == "completed":
                    reply = out.get("result") or "(empty result)"
                else:
                    reply = f"Job `{out['job_id'][:8]}` status: {out['status']} (error: {out.get('error_code')})"
                history.append({"role": "assistant", "content": reply})
                return history, "", f"status: {out['status']} · job: `{out['job_id'][:8]}`"

            send.click(_chat, inputs=[msg, chatbot, user_id], outputs=[chatbot, msg, chat_status])
            msg.submit(_chat, inputs=[msg, chatbot, user_id], outputs=[chatbot, msg, chat_status])
            clear.click(lambda: ([], "", ""), outputs=[chatbot, msg, chat_status])

        with gr.Tab("Jobs"):
            job_id = gr.Textbox(label="Job ID")
            with gr.Row():
                fetch = gr.Button("Fetch status")
                refresh_jobs = gr.Button("Refresh recent")
            job_out = gr.Textbox(label="Result", lines=10)
            jobs_table = gr.Dataframe(
                headers=["id", "status", "request", "result"], label="Recent jobs", wrap=True
            )

            def _fetch(uid: str, jid: str) -> str:
                out = fetch_job(uid, jid)
                if not out.get("ok"):
                    return f"Error: {out.get('error')}"
                return (
                    f"status: {out['status']}\nthread: {out['thread_id']}\n"
                    f"request: {out.get('request')}\nresult: {out.get('result') or ''}\n"
                    f"error: {out.get('error_code') or ''}"
                )

            fetch.click(_fetch, inputs=[user_id, job_id], outputs=job_out)
            refresh_jobs.click(recent_jobs, inputs=user_id, outputs=jobs_table)

        with gr.Tab("Approvals"):
            with gr.Row():
                refresh_ap = gr.Button("Refresh pending")
            ap_table = gr.Dataframe(
                headers=["approval_id", "action", "summary", "created"],
                label="Pending approvals",
                wrap=True,
            )
            ap_id = gr.Dropdown(label="Approval ID", choices=[], interactive=True)
            with gr.Row():
                approve_btn = gr.Button("Approve", variant="primary")
                reject_btn = gr.Button("Reject")
            ap_out = gr.Markdown()

            def _refresh(uid: str):
                rows, choices = pending_approvals(uid)
                return rows, gr.Dropdown(choices=choices, value=choices[0] if choices else None)

            def _decide(uid: str, aid: str, decision: str) -> str:
                out = decide_approval(uid, aid or "", decision)
                if out.get("ok"):
                    return f"{decision.capitalize()}d. Status: {out.get('status')}"
                return f"Error: {out.get('error')}"

            refresh_ap.click(_refresh, inputs=user_id, outputs=[ap_table, ap_id])
            approve_btn.click(
                lambda u, a: _decide(u, a, "approved"), inputs=[user_id, ap_id], outputs=ap_out
            )
            reject_btn.click(
                lambda u, a: _decide(u, a, "rejected"), inputs=[user_id, ap_id], outputs=ap_out
            )

        with gr.Tab("Memory"):
            with gr.Row():
                refresh_mem = gr.Button("Refresh")
            mem_table = gr.Dataframe(
                headers=["id", "category", "content"], label="Memories", wrap=True
            )
            new_content = gr.Textbox(label="New memory", lines=2)
            new_category = gr.Textbox(label="Category", value="general", max_lines=1)
            with gr.Row():
                add_btn = gr.Button("Save memory", variant="primary")
            del_id = gr.Textbox(label="Memory ID to delete", max_lines=1)
            with gr.Row():
                del_btn = gr.Button("Delete")
            mem_out = gr.Markdown()

            refresh_mem.click(list_memories, inputs=user_id, outputs=mem_table)

            def _add(uid: str, content: str, cat: str) -> str:
                out = add_memory(uid, content, cat)
                return (
                    f"Saved `{out['id'][:8]}`." if out.get("ok") else f"Error: {out.get('error')}"
                )

            def _delete(uid: str, mid: str) -> str:
                out = delete_memory(uid, mid)
                return "Deleted." if out.get("ok") else f"Error: {out.get('error')}"

            add_btn.click(_add, inputs=[user_id, new_content, new_category], outputs=mem_out)
            del_btn.click(_delete, inputs=[user_id, del_id], outputs=mem_out)

        with gr.Tab("Briefing"):
            sched = gr.Textbox(label="Scheduled for (optional)", value="", max_lines=1)
            run_btn = gr.Button("Run daily briefing", variant="primary")
            briefing_out = gr.Textbox(label="Briefing", lines=12)

            def _brief(uid: str, s: str) -> str:
                out = run_briefing(uid, s)
                return out.get("result", "") if out.get("ok") else f"Error: {out.get('error')}"

            run_btn.click(_brief, inputs=[user_id, sched], outputs=briefing_out)

        with gr.Tab("Integrations"):
            gr.Markdown(
                "Connect Gmail, WhatsApp, and Zoom. Sends always need approval "
                "(see the Approvals tab). Empty credentials = mock mode."
            )
            with gr.Row():
                refresh_int = gr.Button("Refresh status")
            int_table = gr.Dataframe(
                headers=["provider", "configured", "mode", "description"],
                label="Connected apps",
                wrap=True,
            )
            provider_dd = gr.Dropdown(
                label="Provider", choices=["gmail", "whatsapp", "zoom"], interactive=True
            )
            with gr.Row():
                conn_btn = gr.Button("Connect", variant="primary")
                disconn_btn = gr.Button("Disconnect")
            int_out = gr.Markdown()
            with gr.Row():
                inbox_btn = gr.Button("Preview Gmail inbox")
                meetings_btn = gr.Button("Preview Zoom meetings")
            int_preview = gr.Dataframe(label="Preview", wrap=True)

            refresh_int.click(integration_rows, outputs=int_table)
            conn_btn.click(connect_provider, inputs=[user_id, provider_dd], outputs=int_out)
            disconn_btn.click(disconnect_provider, inputs=[user_id, provider_dd], outputs=int_out)
            inbox_btn.click(gmail_inbox_preview, outputs=int_preview)
            meetings_btn.click(zoom_meetings_preview, outputs=int_preview)

        with gr.Tab("Health"):
            check_btn = gr.Button("Check health")
            health_out = gr.Textbox(label="Status", lines=5)

            def _health() -> str:
                return json.dumps(health_status(), indent=2)

            check_btn.click(_health, outputs=health_out)

    return demo


def mount_frontend(app, path: str = "/ui"):
    """Mount the Gradio UI inside the FastAPI app (lazy import)."""
    import gradio as gr

    demo = build_demo()
    return gr.mount_gradio_app(app, demo, path=path)


def main() -> None:
    import gradio as gr  # noqa: F401  (ensures installed with a clear error otherwise)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    host = os.getenv("PA_UI_HOST", "127.0.0.1")
    port = int(os.getenv("PA_UI_PORT", "7860"))
    build_demo().launch(server_name=host, server_port=port, share=False)


if __name__ == "__main__":
    main()
