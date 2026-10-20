"""Security: user isolation, approval integrity, secret redaction, auth gates."""

import itertools
import json

import pytest

from pa.db import repositories
from pa.db.database import get_session_factory
from pa.security import audit as audit_mod

_SEQ = itertools.count(1)


def _db():
    return get_session_factory()()


def _account(client, name: str = "sec"):
    email = f"{name}{next(_SEQ)}@test.com"
    r = client.post("/api/v1/auth/signup", json={"email": email, "password": "Str0ngPassw0rd"})
    assert r.status_code == 201, r.text
    return {"Cookie": f"pa_session={r.json()['access_token']}"}, r.json()["user"]


# --- Auth gates ---


def test_protected_routes_require_a_session(client):
    for method, path in [
        ("post", "/api/v1/email/sync"),
        ("get", "/api/v1/email/messages"),
        ("get", "/api/v1/email/drafts"),
        ("get", "/api/v1/integrations"),
        ("get", "/api/v1/approvals"),
        ("get", "/api/v1/observability/costs"),
    ]:
        r = getattr(client, method)(path)
        assert r.status_code == 401, f"{method} {path} -> {r.status_code}"


def test_garbage_token_rejected(client):
    r = client.get("/api/v1/email/messages", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


def test_public_catalog_needs_no_auth(client):
    r = client.get("/api/v1/apps")
    assert r.status_code == 200
    assert len(r.json()) > 0


# --- Isolation ---


def test_user_cannot_read_others_job(client):
    hdr_a, user_a = _account(client, "owner")
    r = client.post("/api/v1/assistant/tasks", json={"request": "secret"}, headers=hdr_a)
    job_id = r.json()["job_id"]
    hdr_b, _ = _account(client, "intruder")
    assert client.get(f"/api/v1/jobs/{job_id}", headers=hdr_b).status_code == 403
    assert client.get(f"/api/v1/jobs/{job_id}", headers=hdr_a).status_code == 200


def test_user_cannot_read_others_memory(client):
    hdr_a, _ = _account(client, "mema")
    client.post(
        "/api/v1/memory",
        json={"content": "A pref", "category": "general"},
        headers=hdr_a,
    )
    hdr_b, _ = _account(client, "memb")
    r = client.get("/api/v1/memory", headers=hdr_b)
    assert r.status_code == 200
    assert all(m["content"] != "A pref" for m in r.json())


# --- Approval integrity ---


def test_client_cannot_alter_payload_during_approval(client, fake_model):
    """The stored proposal is authoritative; no client payload is accepted."""
    from pa.queue import tasks as q_tasks

    q_tasks.set_model_override(fake_model)
    hdr, _ = _account(client, "pay")
    r = client.post(
        "/api/v1/assistant/tasks",
        json={"request": "Please send an email to bob"},
        headers=hdr,
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
        before = json.loads(ap.payload)
    finally:
        db.close()

    # A payload in the request body must be ignored entirely.
    client.post(f"/api/v1/approvals/{ap_id}/approve", headers=hdr, json={"payload": {"evil": True}})
    db = _db()
    try:
        stored = json.loads(repositories.get_approval(db, ap_id).payload)
        assert stored == before
        assert "evil" not in stored
    finally:
        db.close()


def test_user_cannot_approve_others_approval(client, fake_model):
    from pa.queue import tasks as q_tasks

    q_tasks.set_model_override(fake_model)
    hdr, _ = _account(client, "appown")
    r = client.post(
        "/api/v1/assistant/tasks",
        json={"request": "Please send an email to bob"},
        headers=hdr,
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
    hdr_intruder, _ = _account(client, "appintr")
    assert (
        client.post(f"/api/v1/approvals/{ap_id}/approve", headers=hdr_intruder).status_code == 403
    )


def test_repeated_approval_cannot_execute_twice(client, fake_model, fake_composio):
    """Duplicate approvals must not deliver a second email."""
    from pa.queue import tasks as q_tasks

    fake_composio.sent.clear()
    q_tasks.set_model_override(fake_model)
    hdr, _ = _account(client, "dupe")
    c = client.post(
        "/api/v1/email/drafts",
        json={"to_addr": "x@y.com", "subject": "hi", "body": "hello"},
        headers=hdr,
    ).json()
    req = client.post(f"/api/v1/email/drafts/{c['id']}/send-request", headers=hdr).json()
    assert (
        client.post(f"/api/v1/approvals/{req['approval_id']}/approve", headers=hdr).status_code
        == 200
    )
    # A second approval submission is rejected as stale/duplicate.
    assert (
        client.post(f"/api/v1/approvals/{req['approval_id']}/approve", headers=hdr).status_code
        == 409
    )
    q_tasks.resume_approved_workflow.apply(args=[req["approval_id"]]).get()
    assert len(fake_composio.sent) == 1


# --- Secrets ---


def test_secrets_absent_from_api_and_audit(client):
    r = client.post(
        "/api/v1/auth/signup", json={"email": "s1@test.com", "password": "Str0ngPassw0rd"}
    )
    assert "Str0ngPassw0rd" not in r.text

    red = audit_mod.redact({"api_key": "SECRET123", "msg": "Authorization: Bearer abc"})
    assert "SECRET123" not in json.dumps(red)
    assert "abc" not in json.dumps(red)


def test_password_hash_never_stored_in_plaintext(db_session):
    from pa.auth import create_user

    user, _ = create_user(db_session, email="hash@test.com", password="Str0ngPassw0rd")
    assert "Str0ngPassw0rd" not in user.password_hash
    assert user.password_hash.startswith("v1$scrypt$")


# --- Browser CORS (SPA on :3000 calling the API on :8000) ---


def test_cors_preflight_allows_authorization_and_credentials(client):
    """The browser rejects credentialed fetches unless this preflight passes."""
    origin = "http://localhost:3000"
    r = client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert r.status_code == 200, r.text
    assert r.headers["access-control-allow-origin"] == origin
    assert r.headers["access-control-allow-credentials"] == "true"
    allowed = {h.strip().lower() for h in r.headers["access-control-allow-headers"].split(",")}
    assert {"authorization", "content-type"} <= allowed


def test_credentialed_login_is_readable_by_the_spa(client):
    """A credentialed signup/login response must be readable cross-origin."""
    origin = "http://localhost:3000"
    r = client.post(
        "/api/v1/auth/signup",
        json={"email": "cors@test.com", "password": "Str0ngPassw0rd"},
        headers={"Origin": origin},
    )
    assert r.status_code == 201, r.text
    assert r.headers["access-control-allow-origin"] == origin
    assert r.headers["access-control-allow-credentials"] == "true"


def test_cors_origin_not_allowlisted_is_refused(client):
    r = client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert "access-control-allow-origin" not in r.headers
