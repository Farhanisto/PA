"""App integrations: mock connectors, allowlisted tools, policy, intents, resume."""

import json

import pytest


def test_connector_status_has_no_secrets():
    from pa.integrations import manager

    for s in manager.connected_accounts():
        assert set(s) == {"provider", "configured", "mode", "description"}
        blob = json.dumps(s).lower()
        for needle in ("password", "secret", "token", "sid"):
            assert needle not in blob


def test_gmail_mock_read_and_send():
    from pa.integrations.gmail import MOCK_OUTBOX, GmailConnector

    MOCK_OUTBOX.clear()
    c = GmailConnector()
    assert c.mode() == "mock"
    inbox = c.read_inbox(2)
    assert len(inbox) == 2
    assert all({"from", "subject", "snippet", "date"} <= set(m) for m in inbox)
    out = c.send_email(to="bob@example.com", subject="Hi", body="Hello")
    assert out["mode"] == "mock"
    assert MOCK_OUTBOX == [{"to": "bob@example.com", "subject": "Hi", "body": "Hello"}]


def test_gmail_send_validates():
    from pa.integrations.gmail import GmailConnector

    c = GmailConnector()
    with pytest.raises(ValueError):
        c.send_email(to="not-an-email", subject="Hi", body="Hello")
    with pytest.raises(ValueError):
        c.send_email(to="a@b.co", subject="", body="Hello")


def test_whatsapp_mock_send_and_validation():
    from pa.integrations.whatsapp import MOCK_OUTBOX, WhatsAppConnector

    MOCK_OUTBOX.clear()
    c = WhatsAppConnector()
    assert c.mode() == "mock"
    out = c.send_message(to="+15550001111", body="Running late!")
    assert out["mode"] == "mock"
    assert MOCK_OUTBOX == [{"to": "+15550001111", "body": "Running late!"}]
    with pytest.raises(ValueError):
        c.send_message(to="", body="hi")
    with pytest.raises(ValueError):
        c.send_message(to="+1", body="x" * 1601)


def test_zoom_mock_list_and_create():
    from pa.integrations.zoom import MOCK_CREATED, ZoomConnector

    MOCK_CREATED.clear()
    c = ZoomConnector()
    meetings = c.list_meetings()
    assert len(meetings) >= 1
    assert all({"id", "topic", "start_time", "join_url"} <= set(m) for m in meetings)
    created = c.create_meeting(topic="Planning", duration_minutes=45)
    assert created["join_url"].startswith("https://")
    assert len(MOCK_CREATED) == 1
    with pytest.raises(ValueError):
        c.create_meeting(topic="")
    with pytest.raises(ValueError):
        c.create_meeting(topic="x", duration_minutes=999)


def test_registry_has_integration_tools():
    from pa.tools.registry import invoke_tool, is_allowed

    for tool in (
        "gmail_read_inbox",
        "gmail_send",
        "whatsapp_read_recent",
        "whatsapp_send",
        "zoom_list_meetings",
        "zoom_create_meeting",
    ):
        assert is_allowed(tool), tool
    with pytest.raises(ValueError, match="not allowed"):
        invoke_tool("gmail_delete_everything", {})
    with pytest.raises(ValueError):
        invoke_tool("gmail_send", {"to": "bad", "subject": "s", "body": "b"})


def test_policy_sends_require_approval():
    from pa.security.policy import requires_approval

    for tool in ("gmail_send", "whatsapp_send", "zoom_create_meeting"):
        assert requires_approval(tool)
    for tool in ("gmail_read_inbox", "zoom_list_meetings", "generate_briefing"):
        assert not requires_approval(tool)


def test_intent_detection():
    from pa.integrations.intents import detect_integration_action

    email = detect_integration_action("Send an email to bob@example.com about lunch tomorrow")
    assert email and email["tool"] == "gmail_send"
    assert email["args"]["to"] == "bob@example.com"

    wa = detect_integration_action("Send a WhatsApp message to +15550001111 about lunch")
    assert wa and wa["tool"] == "whatsapp_send"
    assert "+15550001111" in wa["args"]["to"]

    zoom = detect_integration_action("Schedule a Zoom meeting for sprint planning")
    assert zoom and zoom["tool"] == "zoom_create_meeting"

    assert detect_integration_action("What is 2+2?") is None
    assert detect_integration_action("") is None


def test_propose_action_carries_tool():
    from pa.graph.nodes import propose_action

    out = propose_action({"request": "Send an email to bob@example.com about lunch"})
    assert out["approval_status"] == "pending"
    assert out["proposed_action"]["tool"] == "gmail_send"
    generic = propose_action({"request": "Please send an email to bob"})
    assert "tool" not in generic["proposed_action"]


def test_resume_executes_stored_tool(db_session, fake_model):
    from pa.db import repositories
    from pa.integrations.gmail import MOCK_OUTBOX
    from pa.queue import tasks as q_tasks

    MOCK_OUTBOX.clear()
    q_tasks.set_model_override(fake_model)
    job = repositories.create_job(db_session, user_id="u1", request="Send an email")
    ap = repositories.create_approval(
        db_session,
        user_id="u1",
        job_id=job.id,
        thread_id=job.thread_id,
        action="gmail_send",
        payload={
            "action": "gmail_send",
            "tool": "gmail_send",
            "args": {"to": "bob@example.com", "subject": "Hi", "body": "Hello"},
        },
    )
    repositories.resolve_approval(db_session, ap, status="approved")
    res = q_tasks.resume_approved_workflow.apply(args=[ap.id]).get()
    assert res["ok"] is True
    assert MOCK_OUTBOX and MOCK_OUTBOX[0]["to"] == "bob@example.com"


def test_connect_disconnect_audit(db_session):
    from pa.db import repositories
    from pa.integrations import manager

    with pytest.raises(ValueError, match="not configured"):
        manager.connect(db_session, user_id="u1", provider="gmail")
    with pytest.raises(ValueError, match="unknown provider"):
        manager.connect(db_session, user_id="u1", provider="fax")
    out = manager.disconnect(db_session, user_id="u1", provider="gmail")
    assert out == {"provider": "gmail", "connected": False}
    events = repositories.list_audit_events(db_session, limit=50)
    assert any(e.event_type == "integration.disconnected" for e in events)


def test_integrations_api(client):
    r = client.get("/api/v1/integrations")
    assert r.status_code == 200
    providers = {item["provider"] for item in r.json()}
    assert {"gmail", "whatsapp", "zoom"} <= providers
    # No secrets leak through the API.
    assert "password" not in r.text.lower() and "secret" not in r.text.lower()

    inbox = client.get("/api/v1/integrations/gmail/inbox?limit=2")
    assert inbox.status_code == 200 and len(inbox.json()) == 2

    meetings = client.get("/api/v1/integrations/zoom/meetings")
    assert meetings.status_code == 200 and len(meetings.json()) >= 1

    # Nothing configured in test env → connect is rejected with setup hint.
    bad = client.post("/api/v1/integrations/gmail/connect", headers={"X-User-Id": "u1"})
    assert bad.status_code == 400
