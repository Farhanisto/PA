"""Integration: the assistant never talks about an app the user has not connected.

Reproduces the reported bug end to end: the user asked for HubSpot records,
answered "tickets" to a clarifying question, and the assistant replied about
Zendesk — which was never connected.
"""

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
    email = f"guard{next(_SEQ)}@test.com"
    r = client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": "Str0ngPassw0rd"},
    )
    assert r.status_code == 201, r.text
    return {"Cookie": f"pa_session={r.json()['access_token']}"}


@pytest.fixture()
def catalog_with_zendesk(monkeypatch):
    from pa.integrations import catalog as catalog_mod

    apps = [
        {"slug": "hubspot", "name": "HubSpot", "description": "", "logo": ""},
        {"slug": "zendesk", "name": "Zendesk", "description": "", "logo": ""},
        {"slug": "gmail", "name": "Gmail", "description": "", "logo": ""},
    ]
    monkeypatch.setattr(catalog_mod, "list_apps", lambda limit=None, use_cache=True: list(apps))
    catalog_mod.invalidate_mentions()
    yield apps
    catalog_mod.invalidate_mentions()


class _Msg:
    def __init__(self, content):
        self.content = content


class _Model:
    """Hallucinates Zendesk first, then answers correctly when corrected."""

    def __init__(self):
        self.prompts: list[str] = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        if "CORRECTION" in prompt:
            return _Msg("HubSpot has no open tickets on file.")
        return _Msg("Here are your 3 Zendesk tickets: #1, #2, #3.")


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


def test_the_answer_never_mentions_an_unconnected_app(
    client, account, catalog_with_zendesk
):
    from pa.queue import tasks as q_tasks

    q_tasks.set_model_override(_Model())
    try:
        r = client.post(
            "/api/v1/assistant/tasks",
            json={"request": "show my tickets"},
            headers=account,
        )
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]
        _run(job_id)

        db = _db()
        try:
            job = repositories.get_job(db, job_id)
            assert job.status == "completed", f"{job.status} {job.error_code}"
            result = job.result or ""
        finally:
            db.close()

        assert "Zendesk" not in result, result
    finally:
        q_tasks.clear_model_override()


def test_a_tool_from_another_app_is_never_run(
    client, account, catalog_with_zendesk, fake_composio
):
    """The model may name a tool it was never shown; it must not run."""
    from pa.queue import tasks as q_tasks

    class _Model2:
        def invoke(self, prompt):
            if "Reply with ONE JSON object" in prompt:
                return _Msg(
                    json.dumps(
                        {"action": "tool", "tool": "ZENDESK_LIST_TICKETS", "args": {}}
                    )
                )
            return _Msg("Done.")

    q_tasks.set_model_override(_Model2())
    try:
        # Make Zendesk look callable, so only the app-scoping rule can stop
        # it: the request is about Gmail, which is connected.
        from pa.services import tool_runner

        original = tool_runner.tool_index

        def _index(user_id):
            index = original(user_id)
            index["ZENDESK_LIST_TICKETS"] = {
                "app": "zendesk",
                "description": "List tickets",
                "classification": "read",
                "required": [],
                "properties": {},
            }
            return index

        tool_runner.tool_index = _index
        try:
            r = client.post(
                "/api/v1/assistant/tasks",
                json={"request": "show my gmail emails"},
                headers=account,
            )
            job_id = r.json()["job_id"]
            _run(job_id)
        finally:
            tool_runner.tool_index = original

        assert not any(
            call["tool"].startswith("ZENDESK") for call in fake_composio.executed
        ), fake_composio.executed
    finally:
        q_tasks.clear_model_override()


def test_no_tool_runs_when_the_requested_app_is_not_connected(
    client, account, catalog_with_zendesk, fake_composio
):
    """Asking about an unconnected app must not run some other app's tool."""
    from pa.queue import tasks as q_tasks

    class _Model3:
        def invoke(self, prompt):
            if "Reply with ONE JSON object" in prompt:
                return _Msg(
                    json.dumps(
                        {"action": "tool", "tool": "ZENDESK_LIST_TICKETS", "args": {}}
                    )
                )
            return _Msg("Here are your Zendesk tickets.")

    q_tasks.set_model_override(_Model3())
    try:
        from pa.services import tool_runner

        original = tool_runner.tool_index

        def _index(user_id):
            index = original(user_id)
            index["ZENDESK_LIST_TICKETS"] = {
                "app": "zendesk",
                "description": "List tickets",
                "classification": "read",
                "required": [],
                "properties": {},
            }
            return index

        tool_runner.tool_index = _index
        try:
            # HubSpot is not connected in this environment.
            r = client.post(
                "/api/v1/assistant/tasks",
                json={"request": "show my hubspot tickets"},
                headers=account,
            )
            job_id = r.json()["job_id"]
            _run(job_id)
        finally:
            tool_runner.tool_index = original

        assert fake_composio.executed == [], fake_composio.executed

        db = _db()
        try:
            job = repositories.get_job(db, job_id)
            result = job.result or ""
        finally:
            db.close()
        assert "not connected to HubSpot" in result, result
    finally:
        q_tasks.clear_model_override()
