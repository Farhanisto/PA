"""Frontend service layer: submit/poll, approvals, memory, briefing, health."""

import pytest

gradio = pytest.importorskip("gradio")


def test_submit_and_fetch_roundtrip(client, fake_model):
    from pa.frontend import fetch_job, submit_task

    from pa.queue import tasks as q_tasks

    q_tasks.set_model_override(fake_model)
    out = submit_task("u-fe", "Say hi from UI", timeout_s=30)
    assert out["ok"] is True, out
    assert out["status"] == "completed"
    assert "fake" in (out["result"] or "").lower()
    fetched = fetch_job("u-fe", out["job_id"])
    assert fetched["ok"] is True
    assert fetched["job_id"] == out["job_id"]


def test_submit_empty_request_rejected():
    from pa.frontend import submit_task

    out = submit_task("u-fe", "   ")
    assert out["ok"] is False


def test_fetch_unauthorized_blocked(client, fake_model):
    from pa.frontend import fetch_job, submit_task

    from pa.queue import tasks as q_tasks

    q_tasks.set_model_override(fake_model)
    out = submit_task("owner-fe", "secret task", timeout_s=30)
    assert out["ok"] is True
    denied = fetch_job("intruder", out["job_id"])
    assert denied["ok"] is False and "authorized" in denied["error"]


def test_approval_flow_via_frontend(client, fake_model):
    from pa.frontend import decide_approval, pending_approvals, submit_task

    from pa.queue import tasks as q_tasks

    q_tasks.set_model_override(fake_model)
    out = submit_task("alice-fe", "Please send an email to bob", timeout_s=30)
    assert out["ok"] is True
    assert out["status"] == "waiting_for_approval", out
    rows, choices = pending_approvals("alice-fe")
    assert choices, "expected a pending approval"
    assert any(out["job_id"][:8] in r[0][:8] or r[0] in choices for r in rows)
    res = decide_approval("alice-fe", choices[0], "approved")
    assert res["ok"] is True, res


def test_memory_crud_via_frontend():
    from pa.frontend import add_memory, delete_memory, list_memories

    saved = add_memory("mem-fe", "likes tea", "preference")
    assert saved["ok"] is True
    rows = list_memories("mem-fe")
    assert any(r[0] == saved["id"] for r in rows)
    assert delete_memory("mem-fe", saved["id"])["ok"] is True
    assert all(r[0] != saved["id"] for r in list_memories("mem-fe"))


def test_briefing_via_frontend():
    from pa.frontend import run_briefing
    from pa.queue import tasks as q_tasks

    q_tasks.set_model_override(q_tasks.EchoFakeModel())
    out = run_briefing("brief-fe", "demo-day")
    assert out["ok"] is True and out["result"]


def test_health_status_shape():
    from pa.frontend import health_status

    out = health_status()
    assert out.get("database") == "ok"
    assert "redis" in out


def test_build_demo_smoke():
    from pa.frontend import build_demo

    demo = build_demo()
    assert demo is not None
    assert demo.title == "PA — Personal Assistant"


def test_integrations_tab_services():
    from pa.frontend import (
        connect_provider,
        disconnect_provider,
        gmail_inbox_preview,
        integration_rows,
        zoom_meetings_preview,
    )

    rows = integration_rows()
    assert {r[0] for r in rows} == {"gmail", "whatsapp", "zoom"}
    assert disconnect_provider("u1", "gmail") == "Disconnected gmail."
    assert "Error" in connect_provider("u1", "gmail")  # no creds in test env
    assert connect_provider("u1", "") == "Select a provider."
    assert len(gmail_inbox_preview(2)) == 2
    assert len(zoom_meetings_preview()) >= 1
