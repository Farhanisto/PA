"""Integration: a follow-up answer is understood in the context of its question."""

import itertools

import pytest

from pa.db import repositories
from pa.db.database import get_session_factory

_SEQ = itertools.count(1)


def _db():
    return get_session_factory()()


@pytest.fixture()
def account(client):
    email = f"conv{next(_SEQ)}@test.com"
    r = client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": "Str0ngPassw0rd"},
    )
    assert r.status_code == 201, r.text
    return {"Cookie": f"pa_session={r.json()['access_token']}"}


class _RecordingModel:
    """Records every prompt it is given."""

    def __init__(self):
        self.prompts: list[str] = []

    def invoke(self, prompt):
        self.prompts.append(prompt)

        class Msg:
            content = "Sure — which kind of records?"

        return Msg()


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


def test_a_follow_up_is_given_the_earlier_turn(client, account):
    """The bug: answering "tickets" lost the HubSpot question it answered."""
    from pa.queue import tasks as q_tasks

    model = _RecordingModel()
    q_tasks.set_model_override(model)

    first = client.post(
        "/api/v1/assistant/tasks",
        json={"request": "create new hubspot records"},
        headers=account,
    )
    assert first.status_code == 202, first.text
    conversation_id = first.json()["conversation_id"]
    assert conversation_id

    _run(first.json()["job_id"])

    model.prompts.clear()
    second = client.post(
        "/api/v1/assistant/tasks",
        json={"request": "tickets", "conversation_id": conversation_id},
        headers=account,
    )
    assert second.status_code == 202, second.text
    assert second.json()["conversation_id"] == conversation_id

    _run(second.json()["job_id"])

    joined = "\n".join(model.prompts)
    assert "Conversation so far" in joined, joined[:2000]
    assert "create new hubspot records" in joined, joined[:2000]
    assert "tickets" in joined


def test_a_turn_without_a_conversation_id_starts_a_new_one(client, account):
    first = client.post(
        "/api/v1/assistant/tasks", json={"request": "hello"}, headers=account
    )
    second = client.post(
        "/api/v1/assistant/tasks", json={"request": "again"}, headers=account
    )
    assert first.json()["conversation_id"] != second.json()["conversation_id"]


def test_a_foreign_conversation_id_is_not_honoured(client, account):
    """Guessing another user's conversation id must not join their thread."""
    other = client.post(
        "/api/v1/auth/signup",
        json={"email": f"other{next(_SEQ)}@test.com", "password": "Str0ngPassw0rd"},
    )
    other_headers = {"Cookie": f"pa_session={other.json()['access_token']}"}

    theirs = client.post(
        "/api/v1/assistant/tasks",
        json={"request": "my private request"},
        headers=other_headers,
    )
    their_conversation = theirs.json()["conversation_id"]

    mine = client.post(
        "/api/v1/assistant/tasks",
        json={"request": "hello", "conversation_id": their_conversation},
        headers=account,
    )
    assert mine.json()["conversation_id"] != their_conversation

    db = _db()
    try:
        job = repositories.get_job(db, mine.json()["job_id"])
        assert job.conversation_id != their_conversation
    finally:
        db.close()


def test_conversation_turns_are_grouped_in_the_database(client, account):
    first = client.post(
        "/api/v1/assistant/tasks", json={"request": "one"}, headers=account
    )
    cid = first.json()["conversation_id"]
    second = client.post(
        "/api/v1/assistant/tasks",
        json={"request": "two", "conversation_id": cid},
        headers=account,
    )

    db = _db()
    try:
        turns = repositories.list_jobs_for_conversation(
            db, user_id=_user_id(db), conversation_id=cid
        )
        assert len(turns) == 2
        assert [t.request for t in turns] == ["one", "two"]
    finally:
        db.close()


def _user_id(db) -> str:
    from pa.db.models import User

    return db.query(User).all()[-1].id
