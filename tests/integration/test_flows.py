"""Integration: authenticated assistant flow through API -> Celery(eager) -> DB."""

import itertools

import pytest

from pa.db import repositories
from pa.db.database import get_session_factory

_SEQ = itertools.count(1)


def _db():
    return get_session_factory()()


@pytest.fixture()
def account(client):
    """Create a fresh account and return its auth header."""
    email = f"flow{next(_SEQ)}@test.com"
    r = client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": "Str0ngPassw0rd"},
    )
    assert r.status_code == 201, r.text
    return {"Cookie": f"pa_session={r.json()['access_token']}"}


def test_submit_task_creates_job(client, account):
    r = client.post(
        "/api/v1/assistant/tasks",
        json={"request": "Hello assistant"},
        headers=account,
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["job_id"] and body["thread_id"] and body["status"] == "queued"


def test_run_task_persists_result(client, account, fake_model):
    from pa.queue import tasks as q_tasks

    q_tasks.set_model_override(fake_model)
    r = client.post("/api/v1/assistant/tasks", json={"request": "Say hi"}, headers=account)
    job_id = r.json()["job_id"]
    db = _db()
    try:
        job = repositories.get_job(db, job_id)
        if job.status == "queued":
            db.close()
            q_tasks.run_assistant_job.apply(args=[job_id])
            db = _db()
            job = repositories.get_job(db, job_id)
        assert job.status == "completed", f"status={job.status} err={job.error_code}"
        assert job.result
    finally:
        db.close()


def test_list_jobs_scoped_to_caller(client, account):
    client.post("/api/v1/assistant/tasks", json={"request": "mine"}, headers=account)
    r = client.get("/api/v1/jobs", headers=account)
    assert r.status_code == 200, r.text
    assert any(j["request"] == "mine" for j in r.json())


def test_cors_allows_configured_origin(client):
    r = client.options(
        "/api/v1/assistant/tasks",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
    )
    assert r.status_code == 200, r.text
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"
