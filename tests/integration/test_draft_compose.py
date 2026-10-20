"""Integration: the assistant's reply composer drafts a body from app context."""

from __future__ import annotations

import itertools

import pytest

_SEQ = itertools.count(1)


@pytest.fixture()
def account(client):
    email = f"compose{next(_SEQ)}@test.com"
    r = client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": "Str0ngPassw0rd"},
    )
    assert r.status_code == 201, r.text
    return {"Cookie": f"pa_session={r.json()['access_token']}"}


HUBSPOT_ENGAGEMENT = (
    "Engagement type: EMAIL\n"
    "Subject: (Sample email) Hello there\n"
    "From: sam@cupcakes.example\n"
    "Body: Hey Brian, we're the number-one cupcake supplier."
)


def test_compose_drafts_a_body_from_app_context(client, account):
    r = client.post(
        "/api/v1/email/drafts/compose",
        json={
            "context": HUBSPOT_ENGAGEMENT,
            "sender": "sam@cupcakes.example",
            "subject": "(Sample email) Hello there",
        },
        headers=account,
    )
    assert r.status_code == 200, r.text
    assert isinstance(r.json()["body"], str)
    assert r.json()["body"].strip()


def test_compose_requires_a_context(client, account):
    r = client.post(
        "/api/v1/email/drafts/compose", json={"context": ""}, headers=account
    )
    assert r.status_code == 422, r.text


def test_compose_requires_authentication(client):
    r = client.post("/api/v1/email/drafts/compose", json={"context": "hello"})
    assert r.status_code in (401, 403), r.text


def test_compose_does_not_create_a_draft(client, account):
    """Composing only returns text; a draft is created explicitly."""
    from pa.services import email_service

    before = len(email_service.list_drafts(_db(), user_id=_user_id(account)))
    client.post(
        "/api/v1/email/drafts/compose",
        json={"context": HUBSPOT_ENGAGEMENT},
        headers=account,
    )
    after = len(email_service.list_drafts(_db(), user_id=_user_id(account)))
    assert before == after


def _db():
    from pa.db.database import get_session_factory

    return get_session_factory()()


def _user_id(headers):
    from pa.db.database import get_session_factory

    db = get_session_factory()()
    try:
        from pa.db.models import User

        rows = db.query(User).all()
        return rows[-1].id
    finally:
        db.close()
