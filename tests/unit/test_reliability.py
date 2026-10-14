"""Reliability + observability: ready, stale recovery, briefing seeds, redaction."""

import logging
from datetime import UTC, datetime, timedelta


def test_ready_reports_db_ok(client):
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["checks"]["database"] == "ok"
    assert body["status"] in ("ready",)


def test_health_is_liveness_only(client):
    r = client.get("/health")
    assert r.json() == {"status": "ok"}


def test_correlation_id_header(client):
    r = client.get("/health")
    assert "X-Correlation-Id" in r.headers
    r2 = client.get("/health", headers={"X-Correlation-Id": "abc-123"})
    assert r2.headers["X-Correlation-Id"] == "abc-123"


def test_stale_detection_and_recovery(db_session):
    from pa.db import repositories

    job = repositories.create_job(db_session, user_id="u-stale", request="stuck?")
    # Backdate updated_at to simulate crashed worker.
    job.updated_at = datetime.now(UTC) - timedelta(minutes=60)
    job.status = "running"
    db_session.add(job)
    db_session.commit()

    stale = repositories.list_stale_jobs(db_session, stale_minutes=30)
    assert any(j.id == job.id for j in stale)
    fresh = repositories.list_stale_jobs(db_session, stale_minutes=120)
    assert all(j.id != job.id for j in fresh)


def test_recover_dry_run_lists_without_requeue(db_session):
    from datetime import UTC, datetime, timedelta

    from pa.db import repositories
    from pa.recover import requeue_stale

    job = repositories.create_job(db_session, user_id="u-rec", request="stuck")
    job.updated_at = datetime.now(UTC) - timedelta(minutes=90)
    job.status = "queued"
    db_session.add(job)
    db_session.commit()
    ids = requeue_stale(stale_minutes=30, dry_run=True)
    assert job.id in ids
    # Dry run must not change status.
    db = db_session
    assert repositories.get_job(db, job.id).status == "queued"


def test_queue_depth_reports_db_counts(client):
    from pa.recover import queue_depth

    client.post("/api/v1/assistant/tasks", json={"request": "hi qd"}, headers={"X-User-Id": "u-qd"})
    depth = queue_depth()
    assert "db" in depth and isinstance(depth["db"], dict)
    assert sum(depth["db"].values()) >= 1


def test_briefing_uses_seeded_tasks(db_session):
    from pa.memory.service import save_memory
    from pa.queue import tasks as q_tasks

    # Echo model preserves the draft content (conftest FakeModel is fixed-text).
    q_tasks.set_model_override(q_tasks.EchoFakeModel())
    save_memory(db_session, user_id="u-brief", content="Finish quarterly report", category="task")
    save_memory(db_session, user_id="u-brief", content="Standup moved to 10:00", category="note")
    res = q_tasks.run_daily_briefing.apply(args=["u-brief", "2026-10-14"])
    out = res.get()
    assert out["ok"] is True
    # Seeded content flows into briefing (via draft or fake-model echo of draft).
    assert "quarterly" in out["result"].lower() or "standup" in out["result"].lower()


def test_seed_is_idempotent(db_session):
    from pa.db import repositories
    from pa.seed import seed_user

    seed_user("u-seed")
    n1 = len(repositories.list_memories(db_session, user_id="u-seed"))
    seed_user("u-seed")
    n2 = len(repositories.list_memories(db_session, user_id="u-seed"))
    assert n1 == n2 and n1 >= 5


def test_structured_log_redacts_secrets(caplog):
    from pa.observability import log_event, redact_obj

    assert redact_obj({"api_key": "SECRET", "nested": {"token": "T"}}) == {
        "api_key": "[REDACTED]",
        "nested": {"token": "[REDACTED]"},
    }
    logger = logging.getLogger("pa.test-redact")
    with caplog.at_level(logging.INFO, logger="pa.test-redact"):
        log_event(logger, logging.INFO, "hello", extra={"api_key": "SECRET123"})
    assert "SECRET123" not in caplog.text
    assert "correlation_id" in caplog.text
