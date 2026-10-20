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


def job_id_of(client, account) -> str:
    """A job id belonging to the account."""
    r = client.post("/api/v1/assistant/tasks", json={"request": "hi"}, headers=account)
    return r.json()["job_id"]


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
        r = client.get(f"/api/v1/jobs/{job_id}/artifacts?limit=50", headers=account)
        assert r.status_code == 200, r.text
        cards = r.json()["items"]
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

    r = client.get(f"/api/v1/jobs/{job_id}/artifacts?limit=12", headers=account)
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["items"] == []
    assert page["total"] == 0
    assert page["has_more"] is False


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


def test_artifacts_are_paged(client, account):
    """A briefing can surface far more cards than fit on one screen."""
    from pa.db import repositories
    from pa.db.database import get_session_factory

    me = client.get("/api/v1/auth/me", headers=account).json()["id"]
    db = get_session_factory()()
    try:
        job = repositories.create_job(db, user_id=me, request="r")
        cards = [
            {
                "id": f"hubspot:ticket:{i}",
                "app": "hubspot",
                "kind": "ticket",
                "type": "Ticket",
                "title": f"Ticket {i}",
                "subtitle": "",
                "body": "",
                "fields": [],
                "badges": [],
                "meta": "",
                "group": "Tickets",
                "record_id": str(i),
                "actions": [],
            }
            for i in range(25)
        ]
        repositories.update_job(db, job, status="completed", artifacts=cards)
        job_id = job.id
    finally:
        db.close()

    r = client.get(f"/api/v1/jobs/{job_id}/artifacts?limit=12", headers=account)
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["total"] == 25
    assert len(page["items"]) == 12
    assert page["has_more"] is True

    r = client.get(f"/api/v1/jobs/{job_id}/artifacts?offset=12&limit=12", headers=account)
    page2 = r.json()
    assert page2["total"] == 25
    assert len(page2["items"]) == 12
    assert page2["offset"] == 12
    assert page2["items"][0]["title"] == "Ticket 12"

    r = client.get(f"/api/v1/jobs/{job_id}/artifacts?offset=24&limit=12", headers=account)
    page3 = r.json()
    assert len(page3["items"]) == 1
    assert page3["has_more"] is False


def test_a_job_with_no_records_returns_an_empty_page(client, account):
    r = client.post("/api/v1/assistant/tasks", json={"request": "hello"}, headers=account)
    job_id = r.json()["job_id"]
    r = client.get(f"/api/v1/jobs/{job_id}/artifacts?limit=12", headers=account)
    page = r.json()
    assert page["items"] == []
    assert page["total"] == 0
    assert page["has_more"] is False


def test_the_bare_list_contract_still_works(client, account):
    """An older client sends no limit and must still get a list.

    The API and the web are deployed separately, so the response shape
    cannot simply change under the frontend.
    """
    r = client.get(f"/api/v1/jobs/{job_id_of(client, account)}/artifacts", headers=account)
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


def test_asking_for_a_page_returns_the_envelope(client, account):
    r = client.get(
        f"/api/v1/jobs/{job_id_of(client, account)}/artifacts?offset=0&limit=5",
        headers=account,
    )
    assert r.status_code == 200, r.text
    page = r.json()
    assert set(page) == {"items", "total", "offset", "limit", "has_more"}
