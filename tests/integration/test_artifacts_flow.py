"""Integration: a run's app records reach the chat as structured cards."""

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
    email = f"art{next(_SEQ)}@test.com"
    r = client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": "Str0ngPassw0rd"},
    )
    assert r.status_code == 201, r.text
    return {"Cookie": f"pa_session={r.json()['access_token']}"}


class _Msg:
    def __init__(self, content):
        self.content = content


class _ToolCallingModel:
    """Picks one tool when asked to plan, and answers in prose otherwise."""

    def __init__(self, tool_slug: str, args: dict | None = None):
        self.tool_slug = tool_slug
        self.args = args or {}
        self.calls: list[str] = []

    def invoke(self, prompt):
        self.calls.append(prompt)
        if "Reply with ONE JSON object" in prompt:
            return _Msg(
                json.dumps(
                    {"action": "tool", "tool": self.tool_slug, "args": self.args}
                )
            )
        return _Msg("Here is what I found.")


def test_read_tool_records_become_artifacts(client, account):
    """A run that fetched app data stores cards the chat can render."""
    from pa.queue import tasks as q_tasks

    q_tasks.set_model_override(_ToolCallingModel("GMAIL_FETCH_EMAILS"))
    try:
        r = client.post(
            "/api/v1/assistant/tasks",
            json={"request": "List my emails"},
            headers=account,
        )
        assert r.status_code == 202, r.text
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

        # The records are served as structured cards, not just prose.
        r = client.get(f"/api/v1/jobs/{job_id}/artifacts", headers=account)
        assert r.status_code == 200, r.text
        cards = r.json()
        assert len(cards) == 3, cards
        assert cards[0]["kind"] == "message"
        assert cards[0]["title"] == "URGENT: Invoice #1042 is overdue"
        assert cards[0]["app"] == "gmail"
        assert cards[0]["actions"]
    finally:
        q_tasks.clear_model_override()


def test_artifacts_endpoint_is_scoped_to_the_owner(client, account):
    other = client.post(
        "/api/v1/auth/signup",
        json={"email": f"other{next(_SEQ)}@test.com", "password": "Str0ngPassw0rd"},
    )
    other_headers = {"Cookie": f"pa_session={other.json()['access_token']}"}

    r = client.post(
        "/api/v1/assistant/tasks", json={"request": "hello"}, headers=account
    )
    job_id = r.json()["job_id"]

    r = client.get(f"/api/v1/jobs/{job_id}/artifacts", headers=other_headers)
    assert r.status_code == 403, r.text


def test_artifacts_endpoint_is_empty_when_nothing_was_surfaced(client, account):
    r = client.post(
        "/api/v1/assistant/tasks", json={"request": "hello"}, headers=account
    )
    job_id = r.json()["job_id"]

    r = client.get(f"/api/v1/jobs/{job_id}/artifacts", headers=account)
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_artifacts_endpoint_404s_for_an_unknown_job(client, account):
    r = client.get("/api/v1/jobs/does-not-exist/artifacts", headers=account)
    assert r.status_code == 404, r.text


def test_stored_artifacts_round_trip_through_the_repository(client, account):
    """Cards written on a job are read back intact."""
    db = _db()
    try:
        job = repositories.create_job(db, user_id="u1", request="r")
        cards = [
            {
                "id": "hubspot:engagement:1",
                "app": "hubspot",
                "kind": "engagement",
                "type": "Email",
                "title": "Hello",
                "subtitle": "",
                "body": "body",
                "fields": [{"label": "Engagement ID", "value": "1"}],
                "meta": "",
                "actions": [],
            }
        ]
        repositories.update_job(db, job, status="completed", artifacts=cards)
        assert repositories.job_artifacts(job) == cards
    finally:
        db.close()


def test_corrupt_artifact_json_reads_as_empty():
    """A malformed column must not break the endpoint."""
    from pa.db.models import Job

    job = Job(id="x", user_id="u", thread_id="t", artifacts="not json")
    assert repositories.job_artifacts(job) == []
