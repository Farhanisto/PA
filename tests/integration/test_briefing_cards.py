"""The inbox briefing should be cards, not a wall of text.

A briefing reads triaged messages from the database, so it cannot use the
tool-payload path the app records take. It builds its own cards, which carry
the triage detail a raw Gmail payload does not: priority, whether a reply is
needed, and the deadlines that were found.
"""

import json
from datetime import UTC, datetime, timedelta
from itertools import count

import pytest

from pa.db.database import get_session_factory
from pa.db.models import EmailMessage

_SEQ = count(1)


def _message(**kw):
    base = {
        "id": "m-1",
        "user_id": "u1",
        "provider_message_id": "prov-1",
        "thread_id": "thread-1",
        "from_addr": '"Zao | newline" <us@fullstack.io>',
        "to_addr": "me@example.com",
        "subject": "Why do YOU want $500k",
        "snippet": "A newsletter about making money.",
        "body": "Full text.",
        "received_at": datetime.now(UTC) - timedelta(hours=2),
        "summary": "A newsletter about making money.",
        "priority": "normal",
        "needs_reply": False,
        "action_items": "[]",
        "deadlines": "[]",
    }
    base.update(kw)
    return EmailMessage(**base)


# --- card building ---------------------------------------------------------


def test_a_triaged_message_becomes_a_card():
    from pa.services.artifacts import build_email_cards

    cards = build_email_cards([_message(priority="high", needs_reply=True)])
    assert len(cards) == 1
    card = cards[0]
    assert card["kind"] == "message"
    assert card["type"] == "Email"
    assert card["title"] == "Why do YOU want $500k"
    assert card["subtitle"] == '"Zao | newline" <us@fullstack.io>'
    assert card["body"] == "A newsletter about making money."
    assert card["group"] == "Needs your reply"
    assert card["record_id"] == "thread-1"


def test_the_sender_address_is_extracted_for_a_reply():
    from pa.services.artifacts import build_email_cards

    cards = build_email_cards([_message()])
    reply = next(a for a in cards[0]["actions"] if a["kind"] == "reply")
    assert reply["recipient"] == "us@fullstack.io"
    assert reply["subject"] == "Re: Why do YOU want $500k"


def test_an_already_replied_subject_is_not_prefixed_twice():
    from pa.services.artifacts import build_email_cards

    cards = build_email_cards([_message(subject="Re: Hello")])
    reply = next(a for a in cards[0]["actions"] if a["kind"] == "reply")
    assert reply["subject"] == "Re: Hello"


def test_the_priority_badge_is_coloured_by_priority():
    from pa.services.artifacts import build_email_cards

    high = build_email_cards([_message(priority="high")])[0]
    low = build_email_cards([_message(priority="low")])[0]
    tones = {b["label"]: b["tone"] for b in high["badges"]}
    assert tones["Priority"] == "danger"
    assert {b["label"]: b["tone"] for b in low["badges"]}["Priority"] == "neutral"


def test_needs_reply_gets_its_own_badge():
    from pa.services.artifacts import build_email_cards

    cards = build_email_cards([_message(needs_reply=True)])
    badges = {b["label"]: b["value"] for b in cards[0]["badges"]}
    assert badges["Needs reply"] == "yes"


def test_deadlines_are_shown():
    from pa.services.artifacts import build_email_cards

    cards = build_email_cards([_message(deadlines=json.dumps(["Friday"]))])
    labels = {f["label"]: f["value"] for f in cards[0]["fields"]}
    assert labels["Deadline"] == "Friday"


def test_groups_are_assigned_by_priority():
    from pa.services.artifacts import build_email_cards

    assert build_email_cards([_message(needs_reply=True)])[0]["group"] == "Needs your reply"
    assert build_email_cards([_message(priority="high")])[0]["group"] == "High priority"
    assert build_email_cards([_message()])[0]["group"] == "Normal"


# --- the briefing ----------------------------------------------------------


@pytest.fixture()
def seeded_inbox(client):
    """A signed-in user with a few triaged messages."""
    email = f"brief{next(_SEQ)}@example.com"
    r = client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": "Str0ngPassw0rd"},
    )
    assert r.status_code == 201, r.text
    headers = {"Cookie": f"pa_session={r.json()['access_token']}"}
    user_id = r.json()["user"]["id"]
    tag = next(_SEQ)

    db = get_session_factory()()
    try:
        db.add(_message(id=f"m-hi-{tag}", user_id=user_id, provider_message_id=f"prov-hi-{tag}",
                        thread_id=f"thread-hi-{tag}", priority="high", needs_reply=True,
                        subject="Needs your reply", from_addr="Zao <us@fullstack.io>"))
        db.add(_message(id=f"m-lo-{tag}", user_id=user_id, provider_message_id=f"prov-lo-{tag}",
                        thread_id=f"thread-lo-{tag}", priority="low",
                        subject="Newsletter", from_addr="News <n@example.com>"))
        db.commit()
    finally:
        db.close()
    return headers, user_id


def test_the_briefing_produces_cards(client, seeded_inbox):
    from pa.services import proactive

    headers, user_id = seeded_inbox
    db = get_session_factory()()
    try:
        result = proactive.build_inbox_briefing(db, user_id=user_id)
    finally:
        db.close()

    cards = result["cards"]
    assert cards, "the briefing should produce cards"
    # The newsletter is not worth a card; the one needing a reply is.
    titles = [c["title"] for c in cards]
    assert "Needs your reply" in titles
    assert "Newsletter" not in titles
    assert result["text"].startswith("Inbox Briefing")


def test_the_briefing_job_stores_the_cards(client, seeded_inbox):
    """The chat fetches cards from the job, so they must be stored."""
    from pa.db import repositories
    from pa.queue import tasks as q_tasks

    headers, user_id = seeded_inbox
    r = client.post("/api/v1/assistant/briefing", json={"scheduled_for": ""}, headers=headers)
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]

    db = get_session_factory()()
    try:
        job = repositories.get_job(db, job_id)
        if job.status == "queued":
            db.close()
            q_tasks.run_inbox_briefing.apply(args=[user_id, "", job_id])
            db = get_session_factory()()
            job = repositories.get_job(db, job_id)
        assert job.status == "completed", f"{job.status} {job.error_code}"
    finally:
        db.close()

    r = client.get(f"/api/v1/jobs/{job_id}/artifacts?limit=50", headers=headers)
    assert r.status_code == 200, r.text
    cards = r.json()["items"]
    assert cards, "the briefing should serve cards"
    assert any(c["group"] == "Needs your reply" for c in cards)
    assert any(a["kind"] == "reply" for c in cards for a in c["actions"])
