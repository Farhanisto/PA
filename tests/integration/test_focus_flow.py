"""Integration: a card click focuses the record for the assistant."""

import itertools
import json

import pytest

from pa.db import repositories
from pa.db.database import get_session_factory

_SEQ = itertools.count(1)


def _db():
    return get_session_factory()()


@pytest.fixture()
def account(client):
    email = f"focus{next(_SEQ)}@test.com"
    r = client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": "Str0ngPassw0rd"},
    )
    assert r.status_code == 201, r.text
    return {"Cookie": f"pa_session={r.json()['access_token']}"}


FOCUS = {
    "id": "hubspot:ticket:439347004625",
    "app": "hubspot",
    "kind": "ticket",
    "type": "Ticket",
    "title": "TEST Feature request",
    "record_id": "439347004625",
    "fields": [{"label": "Status", "value": "New"}],
    "meta": "18 Oct 2026, 22:51",
}


class _Msg:
    def __init__(self, content):
        self.content = content


class _RecordingModel:
    def __init__(self):
        self.prompts: list[str] = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return _Msg("Done.")


def _run(job_id: str) -> None:
    from pa.queue import tasks as q_tasks

    db = _db()
    try:
        job = repositories.get_job(db, job_id)
        if job.status == "queued":
            db.close()
            q_tasks.run_assistant_job.apply(args=[job_id])
    finally:
        db.close()


def test_a_focus_is_stored_on_the_job(client, account):
    r = client.post(
        "/api/v1/assistant/tasks",
        json={"request": "make it urgent", "focus": FOCUS},
        headers=account,
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]

    db = _db()
    try:
        job = repositories.get_job(db, job_id)
        stored = repositories.job_focus(job)
    finally:
        db.close()

    assert stored["record_id"] == "439347004625"
    assert stored["title"] == "TEST Feature request"


def test_the_focus_reaches_the_assistant(client, account):
    from pa.queue import tasks as q_tasks

    model = _RecordingModel()
    q_tasks.set_model_override(model)
    try:
        r = client.post(
            "/api/v1/assistant/tasks",
            json={"request": "make it urgent", "focus": FOCUS},
            headers=account,
        )
        job_id = r.json()["job_id"]
        _run(job_id)

        joined = "\n".join(model.prompts)
        assert "439347004625" in joined, joined[:2000]
        assert "TEST Feature request" in joined, joined[:2000]
    finally:
        q_tasks.clear_model_override()


def test_no_focus_means_no_focus_on_the_job(client, account):
    r = client.post(
        "/api/v1/assistant/tasks", json={"request": "hello"}, headers=account
    )
    job_id = r.json()["job_id"]
    db = _db()
    try:
        job = repositories.get_job(db, job_id)
        assert repositories.job_focus(job) == {}
    finally:
        db.close()


def test_a_write_on_a_focused_record_is_approved_with_that_record(
    client, account, fake_composio
):
    """The write must be gated, and the approval must name the record."""
    from pa.queue import tasks as q_tasks

    class _Model:
        def invoke(self, prompt):
            if "Reply with ONE JSON object" in prompt:
                return _Msg(
                    json.dumps(
                        {
                            "action": "tool",
                            "tool": "GMAIL_SEND_EMAIL",
                            "args": {"to": "x@example.com", "subject": "s", "body": "b"},
                        }
                    )
                )
            return _Msg("Ready to send.")

    q_tasks.set_model_override(_Model())
    try:
        r = client.post(
            "/api/v1/assistant/tasks",
            json={"request": "make it urgent", "focus": FOCUS},
            headers=account,
        )
        job_id = r.json()["job_id"]
        _run(job_id)

        db = _db()
        try:
            job = repositories.get_job(db, job_id)
            assert job.status == "waiting_for_approval", job.status
            approvals = repositories.list_approvals_for_user(
                db, user_id=job.user_id, status="pending"
            )
        finally:
            db.close()

        assert len(approvals) == 1
        payload = json.loads(approvals[0].payload)
        assert payload["record_id"] == "439347004625"
        assert "439347004625" in payload["summary"]
        assert "TEST Feature request" in payload["summary"]
    finally:
        q_tasks.clear_model_override()
