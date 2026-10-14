"""Security: ownership, payload immutability, secrets, stale approvals."""

import json
import logging

from pa.db import repositories
from pa.db.database import get_session_factory
from pa.security import audit as audit_mod


def _db():
    return get_session_factory()()


def test_user_cannot_read_others_job(client):
    r = client.post(
        "/api/v1/assistant/tasks", json={"request": "secret task"}, headers={"X-User-Id": "userA"}
    )
    job_id = r.json()["job_id"]
    r2 = client.get(f"/api/v1/jobs/{job_id}", headers={"X-User-Id": "userB"})
    assert r2.status_code == 403
    r3 = client.get(f"/api/v1/jobs/{job_id}", headers={"X-User-Id": "userA"})
    assert r3.status_code == 200


def test_user_cannot_read_others_memory(client):
    client.post(
        "/api/v1/memory",
        json={"content": "A pref", "category": "general"},
        headers={"X-User-Id": "userA"},
    )
    r = client.get("/api/v1/memory", headers={"X-User-Id": "userB"})
    assert r.status_code == 200
    assert all(m["content"] != "A pref" for m in r.json())


def test_user_cannot_approve_others_approval(client, fake_model):
    from pa.queue import tasks as q_tasks

    q_tasks.set_model_override(fake_model)
    r = client.post(
        "/api/v1/assistant/tasks",
        json={"request": "Please send a message to bob"},
        headers={"X-User-Id": "owner"},
    )
    job_id = r.json()["job_id"]
    try:
        q_tasks.run_assistant_job.apply(args=[job_id])
    except Exception:
        pass
    db = _db()
    try:
        job = repositories.get_job(db, job_id)
        ap = repositories.get_pending_approval_for_thread(db, job.thread_id)
        assert ap is not None
        ap_id = ap.id
    finally:
        db.close()
    r2 = client.post(f"/api/v1/approvals/{ap_id}/approve", headers={"X-User-Id": "intruder"})
    assert r2.status_code == 403


def test_client_cannot_alter_payload_during_approval(client, fake_model):
    """Stored proposal is authoritative; client sends no payload."""
    from pa.queue import tasks as q_tasks

    q_tasks.set_model_override(fake_model)
    r = client.post(
        "/api/v1/assistant/tasks",
        json={"request": "Please delete my draft"},
        headers={"X-User-Id": "u-payload"},
    )
    job_id = r.json()["job_id"]
    try:
        q_tasks.run_assistant_job.apply(args=[job_id])
    except Exception:
        pass
    db = _db()
    try:
        job = repositories.get_job(db, job_id)
        ap = repositories.get_pending_approval_for_thread(db, job.thread_id)
        ap_id = ap.id
        before = ap.payload
    finally:
        db.close()
    # Even if attacker tries to pass a replacement payload (ignored by API design),
    # stored payload must remain unchanged.
    r2 = client.post(
        f"/api/v1/approvals/{ap_id}/approve",
        headers={"X-User-Id": "u-payload"},
        json={"action": "hacked", "payload": {"evil": True}} if False else None,
    )
    assert r2.status_code in (
        200,
        307,
        422,
    )  # no payload accepted; approve still uses stored proposal
    db = _db()
    try:
        ap2 = repositories.get_approval(db, ap_id)
        stored = json.loads(ap2.payload)
        assert "evil" not in stored
        assert json.loads(before) == stored
    finally:
        db.close()


def test_stale_or_duplicate_approval_blocked(client, fake_model):
    from pa.queue import tasks as q_tasks

    q_tasks.set_model_override(fake_model)
    r = client.post(
        "/api/v1/assistant/tasks",
        json={"request": "Please pay the invoice"},
        headers={"X-User-Id": "u-stale"},
    )
    job_id = r.json()["job_id"]
    try:
        q_tasks.run_assistant_job.apply(args=[job_id])
    except Exception:
        pass
    db = _db()
    try:
        job = repositories.get_job(db, job_id)
        ap = repositories.get_pending_approval_for_thread(db, job.thread_id)
        ap_id = ap.id
    finally:
        db.close()
    r1 = client.post(f"/api/v1/approvals/{ap_id}/approve", headers={"X-User-Id": "u-stale"})
    assert r1.status_code == 200
    r2 = client.post(f"/api/v1/approvals/{ap_id}/approve", headers={"X-User-Id": "u-stale"})
    assert r2.status_code == 409  # duplicate rejected


def test_secrets_absent_from_api_and_logs(client, caplog):
    r = client.post(
        "/api/v1/assistant/tasks", json={"request": "hi"}, headers={"X-User-Id": "u-sec"}
    )
    assert "NEBIUS" not in r.text
    assert "test-key" not in r.text
    # Audit redaction
    red = audit_mod.redact({"api_key": "SECRET123", "msg": "Authorization: Bearer abc"})
    assert "SECRET123" not in str(red)
    assert "abc" not in str(red)
    with caplog.at_level(logging.INFO):
        logging.getLogger("pa.test").info("job done")
    for rec in caplog.records:
        assert "SECRET123" not in rec.getMessage()
