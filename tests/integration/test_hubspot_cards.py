"""Integration: a HubSpot ticket list arrives as interactive cards, not prose."""

import json
from types import SimpleNamespace

import pytest

from pa.db import repositories
from pa.db.database import get_session_factory

HUBSPOT_TICKETS = {
    "results": [
        {
            "id": 439347004625,
            "createdAt": "2026-10-18T22:51:28.000Z",
            "properties": {
                "subject": "TEST Feature request",
                "content": "I would like a dark mode.",
                "hs_pipeline_stage": "1",
                "hs_pipeline": "0",
                "hs_ticket_priority": "LOW",
                "hs_ticket_category": "PROBLEM",
                "createdate": "2026-10-18T22:51:28.000Z",
                "hs_lastmodifieddate": "2026-10-18T22:51:28.000Z",
            },
        },
        {
            "id": 439503709413,
            "createdAt": "2026-10-18T22:51:28.000Z",
            "properties": {
                "subject": "TEST Login issue",
                "content": "I cannot log in.",
                "hs_pipeline_stage": "1",
                "hs_pipeline": "0",
                "hs_ticket_priority": "HIGH",
                "hs_ticket_category": "PROBLEM",
                "createdate": "2026-10-18T22:51:28.000Z",
                "hs_lastmodifieddate": "2026-10-18T22:51:28.000Z",
            },
        },
    ]
}


PIPELINE_STAGES = {
    "results": [
        {"id": "1", "label": "New", "stageId": "1"},
        {"id": "2", "label": "Working on it", "stageId": "2"},
        {"id": "3", "label": "Waiting on us", "stageId": "3"},
        {"id": "4", "label": "Closed", "stageId": "4"},
    ]
}

PIPELINES = {
    "results": [
        {
            "id": "0",
            "label": "Sales Pipeline",
            "pipelineId": "0",
            "stages": [
                {"id": "1", "label": "New"},
                {"id": "2", "label": "Working on it"},
                {"id": "3", "label": "Waiting on us"},
                {"id": "4", "label": "Closed"},
            ],
        }
    ]
}


def _db():
    return get_session_factory()()


@pytest.fixture()
def account(client):
    r = client.post(
        "/api/v1/auth/signup",
        json={"email": f"hub{next(_id)}@test.com", "password": "Str0ngPassw0rd"},
    )
    assert r.status_code == 201, r.text
    return {"Cookie": f"pa_session={r.json()['access_token']}"}


from itertools import count

_id = count(1)


@pytest.fixture()
def hubspot_connected(fake_composio, monkeypatch):
    """Give the test double a connected HubSpot account and a tickets tool."""

    def _tool(slug, name, desc, props=None):
        return type(
            "T",
            (),
            {
                "slug": slug,
                "name": name,
                "description": desc,
                "is_deprecated": False,
                "input_parameters": {"required": [], "properties": props or {}},
            },
        )()

    fake_composio.tools.append(
        _tool(
            "HUBSPOT_LIST_TICKETS",
            "List tickets",
            "List tickets in HubSpot",
            props={"limit": {"type": "integer"}},
        )
    )
    fake_composio.tools.append(
        _tool(
            "HUBSPOT_RETRIEVE_PIPELINE_STAGES",
            "Get pipeline stages",
            "Get the stages of a HubSpot pipeline",
            props={
                "objectType": {"type": "string"},
                "pipelineId": {"type": "string"},
            },
        )
    )
    fake_composio.tools.append(
        _tool(
            "HUBSPOT_RETRIEVE_ALL_PIPELINES_FOR_SPECIFIED_OBJECT_TYPE",
            "Get pipelines",
            "Get the HubSpot pipelines for an object type",
            props={"objectType": {"type": "string"}},
        )
    )

    class _Accounts:
        def list(self, **_kwargs):
            return SimpleNamespace(
                items=[
                    {"id": "ca-1", "status": "ACTIVE", "toolkit": {"slug": "gmail"}},
                    {"id": "ca-2", "status": "ACTIVE", "toolkit": {"slug": "hubspot"}},
                ]
            )

        def revoke(self, connected_account_id):
            return None

    def _list(toolkit_slug=None, **_kwargs):
        prefix = (toolkit_slug or "").upper()
        items = [t for t in fake_composio.tools if t.slug.startswith(prefix)]
        return type("R", (), {"items": items})()

    real_execute = fake_composio.client.tools.execute

    def _execute(*, tool_slug, arguments, connected_account_id, user_id=None):
        fake_composio.executed.append(
            {
                "tool": tool_slug,
                "user_id": user_id,
                "account": connected_account_id,
            }
        )
        if tool_slug == "HUBSPOT_LIST_TICKETS":
            return HUBSPOT_TICKETS
        if tool_slug == "HUBSPOT_RETRIEVE_PIPELINE_STAGES":
            return PIPELINE_STAGES
        if tool_slug == "HUBSPOT_RETRIEVE_ALL_PIPELINES_FOR_SPECIFIED_OBJECT_TYPE":
            return PIPELINES
        return real_execute(
            tool_slug=tool_slug,
            arguments=arguments,
            connected_account_id=connected_account_id,
            user_id=user_id,
        )

    monkeypatch.setattr(fake_composio.client, "connected_accounts", _Accounts())
    monkeypatch.setattr(fake_composio.client.tools, "list", _list)
    monkeypatch.setattr(fake_composio.client.tools, "execute", _execute)
    return fake_composio


class _Msg:
    def __init__(self, content):
        self.content = content


class _Model:
    def invoke(self, prompt):
        if "Reply with ONE JSON object" in prompt:
            return _Msg(
                json.dumps(
                    {
                        "action": "tool",
                        "tool": "HUBSPOT_LIST_TICKETS",
                        "args": {"limit": 25},
                    }
                )
            )
        return _Msg("There are 2 tickets.")


def test_a_hubspot_ticket_list_becomes_cards(client, account, hubspot_connected):
    from pa.queue import tasks as q_tasks

    q_tasks.set_model_override(_Model())
    try:
        r = client.post(
            "/api/v1/assistant/tasks",
            json={"request": "show my hubspot tickets"},
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
            assert job.status == "completed", f"{job.status} {job.error_code}"
        finally:
            db.close()

        r = client.get(f"/api/v1/jobs/{job_id}/artifacts?limit=50", headers=account)
        assert r.status_code == 200, r.text
        cards = r.json()["items"]

        assert len(cards) == 2, cards
        first = cards[0]
        assert first["kind"] == "ticket"
        assert first["app"] == "hubspot"
        assert first["type"] == "Ticket"
        assert first["title"] == "TEST Feature request"
        assert "dark mode" in first["body"]

        labels = {f["label"]: f["value"] for f in first["fields"]}
        assert labels["Priority"] == "LOW"
        # The raw stage id ("1") is resolved to its name.
        assert labels["Status"] == "New"
        assert labels["Pipeline"] == "Sales Pipeline"
        assert labels["Ticket ID"] == "439347004625"
        assert "2026" in labels["Created"]
        assert first["meta"]

        badges = {b["label"]: b["value"] for b in first["badges"]}
        assert badges["Stage"] == "New"
        assert badges["Priority"] == "LOW"

        # The card is interactive: it offers actions to act on the record.
        assert first["actions"], first
        assert any(a["kind"] == "ask" for a in first["actions"])
        # The action carries the record id, so the write targets it.
        assert any("439347004625" in a.get("prompt", "") for a in first["actions"])
    finally:
        q_tasks.clear_model_override()


def test_the_ticket_tool_is_the_one_that_runs(client, account, hubspot_connected):
    from pa.queue import tasks as q_tasks

    q_tasks.set_model_override(_Model())
    try:
        r = client.post(
            "/api/v1/assistant/tasks",
            json={"request": "show my hubspot tickets"},
            headers=account,
        )
        job_id = r.json()["job_id"]
        db = _db()
        try:
            job = repositories.get_job(db, job_id)
            if job.status == "queued":
                db.close()
                q_tasks.run_assistant_job.apply(args=[job_id])
        finally:
            db.close()

        assert any(
            c["tool"] == "HUBSPOT_LIST_TICKETS" for c in hubspot_connected.executed
        ), hubspot_connected.executed
    finally:
        q_tasks.clear_model_override()
