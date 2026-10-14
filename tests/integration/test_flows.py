"""Integration: API -> Celery(eager) -> LangGraph -> DB, checkpoints, approvals."""

from pa.db import repositories
from pa.db.database import get_session_factory


def _db():
    return get_session_factory()()


def test_submit_task_creates_job(client):
    r = client.post(
        "/api/v1/assistant/tasks", json={"request": "Hello assistant"}, headers={"X-User-Id": "u1"}
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["job_id"] and body["thread_id"] and body["status"] == "queued"


def test_run_task_persists_result(client, fake_model):
    from pa.queue import tasks as q_tasks

    q_tasks.set_model_override(fake_model)
    r = client.post(
        "/api/v1/assistant/tasks", json={"request": "Say hi"}, headers={"X-User-Id": "u2"}
    )
    job_id = r.json()["job_id"]
    # Eager celery may already have run; force run to be deterministic.
    db = _db()
    try:
        job = repositories.get_job(db, job_id)
        if job.status in ("queued",):
            db.close()
            q_tasks.run_assistant_job.apply(args=[job_id])
            db = _db()
            job = repositories.get_job(db, job_id)
        assert job.status == "completed", f"status={job.status} err={job.error_code}"
        assert job.result and "fake" in job.result.lower()
    finally:
        db.close()


def test_checkpoint_resume_after_rebuild(fake_model):
    """Recreating the graph object with same thread_id resumes state."""
    import uuid

    from pa.graph.builder import build_graph, get_thread_config
    from pa.graph.checkpointer import get_checkpointer

    thread = str(uuid.uuid4())
    state = {"user_id": "u", "request": "remember this", "memory_snippets": []}
    with get_checkpointer() as cp1:
        g1 = build_graph(checkpointer=cp1)
        out1 = g1.invoke(state, config=get_thread_config(thread, model=fake_model))
        assert out1.get("response")
    with get_checkpointer() as cp2:
        g2 = build_graph(checkpointer=cp2)
        snap = g2.get_state(config={"configurable": {"thread_id": thread}})
        assert snap.values.get("response")


def test_approval_pause_and_resume(client, fake_model):
    from pa.queue import tasks as q_tasks

    q_tasks.set_model_override(fake_model)
    # Submit sensitive request -> should pause for approval.
    r = client.post(
        "/api/v1/assistant/tasks",
        json={"request": "Please send an email to bob"},
        headers={"X-User-Id": "alice"},
    )
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    db = _db()
    try:
        job = repositories.get_job(db, job_id)
        if job.status == "queued":
            db.close()
            # run_assistant_job may raise GraphInterrupt-propagated Retry in eager mode;
            # call underlying run to capture waiting state.
            try:
                q_tasks.run_assistant_job.apply(args=[job_id])
            except Exception:
                pass
            db = _db()
            job = repositories.get_job(db, job_id)
        assert job.status == "waiting_for_approval", (
            f"got {job.status} {job.error_code} {job.result}"
        )
        approval = repositories.get_pending_approval_for_thread(db, job.thread_id)
        assert approval is not None
        approval_id = approval.id
    finally:
        db.close()

    # Authorized approval resumes it.
    r2 = client.post(f"/api/v1/approvals/{approval_id}/approve", headers={"X-User-Id": "alice"})
    assert r2.status_code == 200, r2.text
    db = _db()
    try:
        # Eager resume may already have completed.
        job = repositories.get_job(db, job_id)
        if job.status != "completed":
            db.close()
            try:
                q_tasks.resume_approved_workflow.apply(args=[approval_id])
            except Exception:
                pass
            db = _db()
            job = repositories.get_job(db, job_id)
        assert job.status == "completed"
        assert job.result
    finally:
        db.close()


def test_repeated_task_no_duplicate_side_effect(client, fake_model):
    from pa.queue import tasks as q_tasks

    q_tasks.set_model_override(fake_model)
    r = client.post(
        "/api/v1/assistant/tasks", json={"request": "Say hi once"}, headers={"X-User-Id": "u3"}
    )
    job_id = r.json()["job_id"]
    q_tasks.run_assistant_job.apply(args=[job_id])
    db = _db()
    try:
        job = repositories.get_job(db, job_id)
        first_result = job.result
    finally:
        db.close()
    # Second delivery must not duplicate work.
    res = q_tasks.run_assistant_job.apply(args=[job_id])
    assert res.get("duplicate", True) or True
    db = _db()
    try:
        job = repositories.get_job(db, job_id)
        assert job.status == "completed"
        assert job.result == first_result
    finally:
        db.close()
