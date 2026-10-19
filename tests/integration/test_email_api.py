"""API-level tests: auth, email, drafts, approval-gated send, observability, evals."""

from __future__ import annotations

import itertools

import pytest

_EMAIL_SEQ = itertools.count(1)


@pytest.fixture()
def strict_client(monkeypatch):
    """Client that always requires a real session, so auth is enforced."""
    monkeypatch.setenv("PA_ALLOW_DEMO_USER", "0")
    from pa.config import reset_settings_cache
    from pa.db.database import reset_engine
    from fastapi.testclient import TestClient

    from pa.app import create_app

    reset_settings_cache()
    reset_engine()
    with TestClient(create_app()) as c:
        yield c


def _signup(client, email=None, password="Str0ngPassw0rd", name="Alice"):
    # Unique email per call: the test DB is shared across tests in a session.
    email = email or f"user{next(_EMAIL_SEQ)}@test.com"
    r = client.post(
        "/api/v1/auth/signup", json={"email": email, "password": password, "display_name": name}
    )
    assert r.status_code == 201, r.text
    return {"Cookie": f"pa_session={r.json()['access_token']}"}, r.json()["user"]


# --- Auth ---


def test_signup_login_me_logout(strict_client):
    c = strict_client
    hdr, user = _signup(c)
    email = user["email"]
    assert user["email"].endswith("@test.com")
    assert user["permissions"]["email.auto_send"] is False  # safe default

    me = c.get("/api/v1/auth/me", headers=hdr)
    assert me.status_code == 200 and me.json()["id"] == user["id"]

    login = c.post("/api/v1/auth/login", json={"email": email, "password": "Str0ngPassw0rd"})
    assert login.status_code == 200

    out = c.post("/api/v1/auth/logout", headers=hdr)
    assert out.status_code == 204
    assert c.get("/api/v1/auth/me", headers=hdr).status_code == 401


def test_duplicate_signup_conflict(strict_client):
    _, user = _signup(strict_client)
    again = strict_client.post(
        "/api/v1/auth/signup", json={"email": user["email"], "password": "Str0ngPassw0rd"}
    )
    assert again.status_code == 400


def test_weak_password_rejected(strict_client):
    r = strict_client.post("/api/v1/auth/signup", json={"email": "x@test.com", "password": "weak"})
    assert r.status_code == 400


def test_unauthenticated_blocked(strict_client):
    for method, path in [
        ("post", "/api/v1/email/sync"),
        ("get", "/api/v1/email/messages"),
        ("get", "/api/v1/email/drafts"),
        ("get", "/api/v1/observability/costs"),
    ]:
        r = getattr(strict_client, method)(path)
        assert r.status_code == 401, f"{method} {path} -> {r.status_code}"


def test_bearer_token_accepted(strict_client):
    _, user = _signup(strict_client)
    r = strict_client.post(
        "/api/v1/auth/login", json={"email": user["email"], "password": "Str0ngPassw0rd"}
    )
    token = r.json()["access_token"]
    me = strict_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200


def test_permission_toggle(strict_client):
    hdr, user = _signup(strict_client)
    r = strict_client.put(
        "/api/v1/auth/permissions/email.read", json={"granted": False}, headers=hdr
    )
    assert r.status_code == 200
    # With read revoked, sync must be denied.
    assert strict_client.post("/api/v1/email/sync", headers=hdr).status_code == 403
    strict_client.put("/api/v1/auth/permissions/email.read", json={"granted": True}, headers=hdr)
    assert strict_client.post("/api/v1/email/sync", headers=hdr).status_code == 200


# --- Email ---


def test_sync_and_list_messages(strict_client):
    c = strict_client
    hdr, _ = _signup(c)
    sync = c.post("/api/v1/email/sync?limit=5", headers=hdr)
    assert sync.status_code == 200
    assert sync.json()["fetched"] == 3

    msgs = c.get("/api/v1/email/messages", headers=hdr).json()
    assert len(msgs) == 3
    priorities = {m["priority"] for m in msgs}
    assert "high" in priorities and "low" in priorities
    # Deadlines extracted on the urgent one.
    urgent = [m for m in msgs if m["priority"] == "high"][0]
    assert urgent["deadlines"]


def test_messages_filter_by_priority(strict_client):
    c = strict_client
    hdr, _ = _signup(c)
    c.post("/api/v1/email/sync", headers=hdr)
    high = c.get("/api/v1/email/messages?priority=high", headers=hdr).json()
    assert len(high) == 1 and high[0]["priority"] == "high"
    followups = c.get("/api/v1/email/followups", headers=hdr).json()
    assert len(followups) == 1


def test_cross_user_isolation(strict_client):
    c = strict_client
    hdr_a, _ = _signup(c)
    c.post("/api/v1/email/sync", headers=hdr_a)
    msg = c.get("/api/v1/email/messages", headers=hdr_a).json()[0]

    hdr_b, _ = _signup(c, name="Bob")
    # Cannot read A's message.
    assert c.get(f"/api/v1/email/messages/{msg['id']}", headers=hdr_b).status_code == 404
    # B's list is empty.
    assert c.get("/api/v1/email/messages", headers=hdr_b).json() == []


# --- Drafts + approval-gated send --


def test_draft_lifecycle_and_approval_send(strict_client, fake_composio):
    c = strict_client
    hdr, _ = _signup(c)
    c.post("/api/v1/email/sync", headers=hdr)
    msg = [m for m in c.get("/api/v1/email/messages", headers=hdr).json() if m["needs_reply"]][0]

    gen = c.post(
        "/api/v1/email/drafts/generate",
        json={"message_id": msg["id"], "instructions": "accept Thursday"},
        headers=hdr,
    )
    assert gen.status_code == 201, gen.text
    draft = gen.json()
    assert draft["to_addr"] == "sam@team.example"

    # User edits the draft.
    edited = c.patch(
        f"/api/v1/email/drafts/{draft['id']}",
        json={"body": "Edited by the user before sending."},
        headers=hdr,
    )
    assert edited.status_code == 200
    assert "Edited by the user" in edited.json()["body"]

    # Request approval to send.
    req = c.post(f"/api/v1/email/drafts/{draft['id']}/send-request", headers=hdr)
    assert req.status_code == 200
    approval_id = req.json()["approval_id"]

    # Duplicate request is idempotent.
    req2 = c.post(f"/api/v1/email/drafts/{draft['id']}/send-request", headers=hdr)
    assert req2.json()["approval_id"] == approval_id

    # Not sent before approval: the worker must refuse while still pending.
    fake_composio.sent.clear()
    from pa.queue import tasks as q_tasks

    premature = q_tasks.resume_approved_workflow.apply(args=[approval_id]).get()
    assert premature["ok"] is False and "not approved" in premature["error"]
    assert fake_composio.sent == []

    # Explicit approval, then resume sends it.
    ap_resp = c.post(f"/api/v1/approvals/{approval_id}/approve", headers=hdr)
    assert ap_resp.status_code == 200, ap_resp.text

    ap = q_tasks.resume_approved_workflow.apply(args=[approval_id]).get()
    assert ap["ok"] is True, ap
    assert len(fake_composio.sent) == 1
    assert "Edited by the user" in fake_composio.sent[0]["body"]
    assert c.get(f"/api/v1/email/drafts/{draft['id']}", headers=hdr).json()["status"] == "sent"

    # A second resume must not send again.
    again = q_tasks.resume_approved_workflow.apply(args=[approval_id]).get()
    assert again["ok"] is True
    assert len(fake_composio.sent) == 1


def test_rejected_draft_is_not_sent(strict_client, fake_composio):
    c = strict_client
    hdr, _ = _signup(c)
    draft = c.post(
        "/api/v1/email/drafts",
        json={"to_addr": "x@y.com", "subject": "hi", "body": "hello"},
        headers=hdr,
    ).json()
    req = c.post(f"/api/v1/email/drafts/{draft['id']}/send-request", headers=hdr).json()
    r = c.post(f"/api/v1/approvals/{req['approval_id']}/reject", headers=hdr)
    assert r.status_code == 200

    fake_composio.sent.clear()
    from pa.queue import tasks as q_tasks

    out = q_tasks.resume_approved_workflow.apply(args=[req["approval_id"]]).get()
    assert out["ok"] is False  # not approved
    assert fake_composio.sent == []


def test_send_requires_permission(strict_client):
    c = strict_client
    hdr, _ = _signup(c)
    draft = c.post(
        "/api/v1/email/drafts", json={"to_addr": "x@y.com", "body": "hi"}, headers=hdr
    ).json()
    # Revoke send permission.
    c.put("/api/v1/auth/permissions/email.send", json={"granted": False}, headers=hdr)
    req = c.post(f"/api/v1/email/drafts/{draft['id']}/send-request", headers=hdr)
    assert req.status_code == 403


# --- Observability ---


def test_observability_records_traces(strict_client):
    c = strict_client
    hdr, _ = _signup(c)
    sync = c.post("/api/v1/email/sync", headers=hdr)
    assert sync.status_code == 200

    calls = c.get("/api/v1/observability/tool-calls", headers=hdr).json()
    assert len(calls) >= 1
    assert calls[0]["tool_name"] == "gmail_fetch_inbox"

    costs = c.get("/api/v1/observability/costs", headers=hdr).json()
    assert "total_tokens" in costs


def test_observability_isolated(strict_client):
    c = strict_client
    hdr_a, _ = _signup(c)
    c.post("/api/v1/email/sync", headers=hdr_a)
    hdr_b, _ = _signup(c, name="B")
    # B sees no tool calls from A.
    assert c.get("/api/v1/observability/tool-calls", headers=hdr_b).json() == []


# --- Evals ---


def test_eval_suites_run(strict_client):
    c = strict_client
    hdr, _ = _signup(c)
    suites = c.get("/api/v1/evals/suites", headers=hdr).json()
    assert set(suites) == {"classification", "summary", "draft", "authorization"}
    for suite in suites:
        r = c.get(f"/api/v1/evals/{suite}", headers=hdr)
        assert r.status_code == 200, r.text
        assert r.json()["failed"] == 0, r.text


def test_eval_run_all(strict_client):
    c = strict_client
    hdr, _ = _signup(c)
    r = c.post("/api/v1/evals/run", headers=hdr)
    assert r.status_code == 200
    body = r.json()
    assert body["failed"] == 0
    assert body["score"] == 1.0


# --- No placeholder data: nothing runs without an approved app connection ---


def test_sync_refuses_when_app_not_connected(strict_client, fake_composio):
    """No connection -> a clear 409, never invented mail."""
    c = strict_client
    hdr, _ = _signup(c)
    fake_composio.connected = False

    r = c.post("/api/v1/email/sync", headers=hdr)
    assert r.status_code == 409, r.text
    assert "not connected" in r.json()["detail"].lower()

    # Nothing was written to the user's inbox.
    assert c.get("/api/v1/email/messages", headers=hdr).json() == []


def test_send_refuses_when_app_not_connected(strict_client, fake_composio):
    c = strict_client
    hdr, _ = _signup(c)
    draft = c.post(
        "/api/v1/email/drafts",
        json={"to_addr": "x@y.com", "subject": "hi", "body": "hello"},
        headers=hdr,
    ).json()
    req = c.post(f"/api/v1/email/drafts/{draft['id']}/send-request", headers=hdr).json()

    # Drop the connection before approving: approving resumes the workflow
    # eagerly, so this is where the send would be attempted.
    fake_composio.connected = False
    c.post(f"/api/v1/approvals/{req['approval_id']}/approve", headers=hdr)
    assert fake_composio.sent == []

    from pa.queue import tasks as q_tasks

    out = q_tasks.resume_approved_workflow.apply(args=[req["approval_id"]]).get()
    assert out["ok"] is False
    assert fake_composio.sent == []


def test_catalog_is_empty_without_composio_key(strict_client, monkeypatch):
    """No hardcoded fallback list: an unconfigured server offers nothing."""
    from pa.config import reset_settings_cache
    from pa.integrations import composio_client

    monkeypatch.setenv("COMPOSIO_API_KEY", "")
    composio_client.clear_client_factory()
    reset_settings_cache()
    try:
        assert strict_client.get("/api/v1/apps").json() == []
        status = strict_client.get("/api/v1/apps/status").json()
        assert status["configured"] is False
        assert "not set" in status["reason"]
    finally:
        reset_settings_cache()


def test_sync_reports_unconfigured_composio_honestly(strict_client, monkeypatch):
    """A missing COMPOSIO_API_KEY must be a clear 409, not an opaque 500."""
    from pa.config import reset_settings_cache
    from pa.integrations import composio_client

    # An env var (even empty) outranks the on-disk .env in pydantic Settings,
    # so this is deterministic regardless of what a developer has configured.
    monkeypatch.setenv("COMPOSIO_API_KEY", "")
    composio_client.clear_client_factory()
    reset_settings_cache()
    try:
        c = strict_client
        hdr, _ = _signup(c)
        r = c.post("/api/v1/email/sync", headers=hdr)
        assert r.status_code == 409, r.text
        assert "composio" in r.json()["detail"].lower()
    finally:
        reset_settings_cache()


def _composio_exc(cls, message, status):
    """Build a real Composio SDK error (they require response + body)."""
    import httpx

    request = httpx.Request("POST", "https://backend.composio.dev/api/v3/connected_accounts/link")
    response = httpx.Response(status, json={"error": {"message": message}}, request=request)
    return cls(message, response=response, body=None)


def test_composio_permission_error_is_actionable_not_500(strict_client, fake_composio):
    """A read-only Composio key must yield 502 + guidance, never an opaque 500."""
    from composio_client import PermissionDeniedError

    def deny(**_kwargs):
        raise _composio_exc(
            PermissionDeniedError,
            "This API key does not have the permissions required for "
            "POST /api/v3/connected_accounts/link.",
            403,
        )

    fake_composio.client.link.create = deny

    c = strict_client
    hdr, _ = _signup(c)
    r = c.post("/api/v1/integrations/gmail/connect", headers=hdr)
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert "lacks permission" in detail.lower()
    assert "scopes" in detail.lower()


def test_composio_auth_error_is_actionable_not_500(strict_client, fake_composio):
    from composio_client import AuthenticationError

    def deny(**_kwargs):
        raise _composio_exc(AuthenticationError, "Invalid API key", 401)

    fake_composio.client.link.create = deny
    c = strict_client
    hdr, _ = _signup(c)
    r = c.post("/api/v1/integrations/gmail/connect", headers=hdr)
    assert r.status_code == 502
    assert "rejected" in r.json()["detail"].lower()


def test_tools_endpoint_lists_toolkit_tools(strict_client, fake_composio):
    from pa.integrations import catalog as catalog_mod

    catalog_mod._tool_cache.clear()
    c = strict_client
    hdr, _ = _signup(c)

    class Tool:
        slug = "GMAIL_SEND_EMAIL"
        name = "Gmail Send Email"
        description = "Sends mail"

    fake_composio.client.tools.list = lambda **k: type("R", (), {"items": [Tool()]})()

    r = c.get("/api/v1/integrations/gmail/tools", headers=hdr)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "gmail"
    assert body["tool_count"] == 1
    assert body["tools"][0]["slug"] == "GMAIL_SEND_EMAIL"
    assert body["connected"] is True


def test_every_enqueued_task_name_exists_in_the_registry():
    """Guards the stuck-queue bug: the route enqueued a task that was never defined."""
    import ast
    import pathlib as _pl

    import pa.queue.tasks as tasks_mod

    registered = set(tasks_mod.celery_app.tasks)
    src = _pl.Path(tasks_mod.__file__).read_text()

    # Every `X.delay(...)` in the API must name a task that actually exists.
    routes_dir = _pl.Path(tasks_mod.__file__).parents[1] / "api" / "routes"
    route_files = sorted(routes_dir.glob("*.py"))
    assert route_files, f"no route modules found under {routes_dir}"
    for route in route_files:
        text = route.read_text()
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr != "delay":
                    continue
                name = getattr(node.func.value, "id", None)
                if not name:
                    continue
                assert f"def {name}(" in src, (
                    f"{route.name} enqueues '{name}' which is not defined in tasks.py"
                )

    # And the registry agrees with the decorators.
    for required in ("pa.run_inbox_briefing", "pa.run_assistant_job", "pa.resume_approved_workflow"):
        assert required in registered, f"{required} missing from the Celery registry"


# --- Per-user proactive schedule ---


def test_schedule_api_roundtrip(client):
    hdr, _ = _signup(client)
    r = client.get("/api/v1/settings/schedule", headers=hdr)
    assert r.status_code == 200, r.text
    assert r.json()["briefing_hour"] == 8
    assert r.json()["timezone"] == "UTC"

    r = client.put(
        "/api/v1/settings/schedule",
        json={"briefing_hour": 6, "briefing_minute": 45, "timezone": "Europe/London"},
        headers=hdr,
    )
    assert r.status_code == 200, r.text
    assert r.json()["briefing_hour"] == 6
    assert r.json()["briefing_minute"] == 45
    assert r.json()["timezone"] == "Europe/London"


def test_schedule_rejects_out_of_range_and_bad_timezone(client):
    hdr, _ = _signup(client)
    assert client.put("/api/v1/settings/schedule", json={"briefing_hour": 99}, headers=hdr).status_code == 422
    assert client.put("/api/v1/settings/schedule", json={"timezone": "Mars/Olympus"}, headers=hdr).status_code == 400


def test_schedule_is_per_user(client):
    a_hdr, _ = _signup(client, "alpha@example.com")
    b_hdr, _ = _signup(client, "beta@example.com")
    client.put("/api/v1/settings/schedule", json={"briefing_hour": 5}, headers=a_hdr)
    assert client.get("/api/v1/settings/schedule", headers=b_hdr).json()["briefing_hour"] == 8


def test_tools_endpoint_is_generic_for_any_app(strict_client, fake_composio):
    """One interface for all apps: a non-Google toolkit must work identically."""
    from pa.integrations import catalog as catalog_mod

    catalog_mod._tool_cache.clear()

    class Tool:
        def __init__(self, slug, name):
            self.slug, self.name, self.description = slug, name, "crm op"

    fake_composio.client.tools.list = lambda **k: type(
        "R", (), {"items": [Tool("HUBSPOT_LIST_DEALS", "List deals")]}
    )()

    c = strict_client
    hdr, _ = _signup(c)
    r = c.get("/api/v1/integrations/hubspot/tools", headers=hdr)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "hubspot"
    assert body["tool_count"] == 1
    assert body["tools"][0]["slug"] == "HUBSPOT_LIST_DEALS"


def test_tools_endpoint_rejects_junk_provider(strict_client):
    c = strict_client
    hdr, _ = _signup(c)
    assert c.get("/api/v1/integrations/bad%3Bslug/tools", headers=hdr).status_code in (400, 404, 422)


def test_read_only_tool_executes_without_approval(strict_client, fake_composio):
    """A LIST tool runs immediately and feeds the summary."""
    from pa.db.database import get_session_factory
    from pa.db import repositories

    from pa.auth.service import create_user

    uid = create_user(
        get_session_factory()(), email="tools@example.com", password="Str0ngPassw0rd"
    )[0].id

    from pa.services import tool_runner

    index = tool_runner.tool_index(uid)
    assert "GMAIL_FETCH_EMAILS" in index, index.keys()
    assert index["GMAIL_FETCH_EMAILS"]["classification"] == "read"

    out = tool_runner.execute(uid, "GMAIL_FETCH_EMAILS", {})
    assert out["tool"] == "GMAIL_FETCH_EMAILS"
    assert "id" in out["result"]
    # Both ids must be sent; Composio 400s without user_id.
    call = fake_composio.executed[-1]
    assert call["user_id"] == uid and call["account"] == "ca-1"


def test_execute_refuses_when_nothing_connected(fake_composio):
    from pa.services import tool_runner

    fake_composio.connected = False
    with pytest.raises(tool_runner.ToolError) as e:
        tool_runner.execute("u1", "GMAIL_FETCH_EMAILS", {})
    assert "no connected apps" in str(e.value).lower()


def test_approved_write_tool_executes(strict_client, fake_composio):
    from pa.auth.service import create_user
    """After approval the exact tool + args run, and the job records it."""
    from pa.auth.service import create_user
    from pa.auth.service import create_user
    from pa.auth.service import create_user
    from pa.db.database import get_session_factory
    from pa.db import repositories
    from pa.queue import tasks as q_tasks

    db = get_session_factory()()
    user = create_user(db, email="writer@example.com", password="Str0ngPassw0rd")[0]
    job = repositories.create_job(db, user_id=user.id, request="create a deal")
    approval = repositories.create_approval(
        db, job_id=job.id, thread_id=job.thread_id, user_id=user.id, action="composio_tool",
        payload={"tool": "GMAIL_SEND_EMAIL", "app": "gmail",
                 "args": {"to": "a@b.com", "subject": "s", "body": "b"}},
    )
    db.commit()

    out = q_tasks._execute_approved_tool(
        db, job, approval, {"tool": "GMAIL_SEND_EMAIL", "app": "gmail",
                            "args": {"to": "a@b.com", "subject": "s", "body": "b"}}
    )
    assert out["ok"] is True, out
    assert out["tool"] == "GMAIL_SEND_EMAIL"
    assert fake_composio.executed[-1]["user_id"] == user.id


def test_approved_payload_cannot_be_widened_to_another_app(strict_client, fake_composio):
    from pa.auth.service import create_user
    """A tampered approval must not run a tool from an unconnected app."""
    from pa.db.database import get_session_factory
    from pa.db import repositories
    from pa.queue import tasks as q_tasks

    db = get_session_factory()()
    user = create_user(db, email="tamper@example.com", password="Str0ngPassw0rd")[0]
    job = repositories.create_job(db, user_id=user.id, request="x")
    db.commit()

    out = q_tasks._execute_approved_tool(
        db, job, None, {"tool": "HUBSPOT_DELETE_ALL_COMPANIES", "args": {}}
    )
    assert out["ok"] is False
    assert "not available" in out["error"]


def test_read_tool_cannot_be_executed_through_the_approval_path(strict_client, fake_composio):
    from pa.auth.service import create_user
    """Only writes may run via approval; a read must not slip through."""
    from pa.db.database import get_session_factory
    from pa.db import repositories
    from pa.queue import tasks as q_tasks

    db = get_session_factory()()
    user = create_user(db, email="readguy@example.com", password="Str0ngPassw0rd")[0]
    job = repositories.create_job(db, user_id=user.id, request="x")
    db.commit()

    out = q_tasks._execute_approved_tool(db, job, None, {"tool": "GMAIL_FETCH_EMAILS", "args": {}})
    assert out["ok"] is False
    assert "not a write tool" in out["error"]
