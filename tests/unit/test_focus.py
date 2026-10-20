"""Unit tests: the record the user clicked becomes the subject of the request."""

import json

import pytest


class _Msg:
    def __init__(self, content):
        self.content = content


class _Model:
    def __init__(self, text=""):
        self.text = text
        self.prompts: list[str] = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return _Msg(self.text or "{}")


FOCUS = {
    "id": "hubspot:ticket:439347004625",
    "app": "hubspot",
    "kind": "ticket",
    "type": "Ticket",
    "title": "TEST Feature request",
    "record_id": "439347004625",
    "fields": [
        {"label": "Status", "value": "New"},
        {"label": "Priority", "value": "HIGH"},
    ],
    "meta": "18 Oct 2026, 22:51",
}


# --- the prompt ------------------------------------------------------------


def test_the_focus_is_in_the_prompt():
    from pa.graph.nodes import _prompt_for

    prompt = _prompt_for({"request": "make it urgent", "focus": FOCUS})
    assert "TEST Feature request" in prompt
    assert "439347004625" in prompt
    assert "HubSpot" in prompt or "hubspot" in prompt


def test_the_focus_lists_the_current_values():
    from pa.graph.nodes import _prompt_for

    prompt = _prompt_for({"request": "make it urgent", "focus": FOCUS})
    assert "Status: New" in prompt
    assert "Priority: HIGH" in prompt


def test_the_prompt_tells_the_model_to_use_the_record_id():
    from pa.graph.nodes import _prompt_for

    prompt = _prompt_for({"request": "make it urgent", "focus": FOCUS})
    assert "use the id above" in prompt.lower()


def test_no_focus_means_no_focus_block():
    from pa.graph.nodes import _prompt_for

    prompt = _prompt_for({"request": "make it urgent"})
    assert "Use the id above" not in prompt
    assert "acting on one specific" not in prompt


# --- tool selection --------------------------------------------------------


@pytest.fixture()
def indexed(monkeypatch):
    from pa.services import tool_runner

    index = {
        "HUBSPOT_UPDATE_TICKET": {
            "app": "hubspot",
            "description": "Update a ticket in HubSpot",
            "classification": "write",
            "required": [],
            "properties": {},
        },
        "HUBSPOT_LIST_TICKETS": {
            "app": "hubspot",
            "description": "List tickets in HubSpot",
            "classification": "read",
            "required": [],
            "properties": {},
        },
    }
    monkeypatch.setattr(tool_runner, "tool_index", lambda user_id: dict(index))


def _plan(model, state):
    from pa.graph.nodes import plan_action

    return plan_action(state, config={"configurable": {"model": model}})


def test_the_tool_prompt_carries_the_record(indexed):
    """The model must see which record it is acting on."""
    model = _Model(
        json.dumps(
            {
                "action": "tool",
                "tool": "HUBSPOT_UPDATE_TICKET",
                "args": {"id": "439347004625", "properties": {"hs_ticket_priority": "HIGH"}},
            }
        )
    )
    _plan(
        model,
        {
            "user_id": "u1",
            "request": "set the priority to urgent",
            "connected_apps": ["hubspot"],
            "focus": FOCUS,
        },
    )
    prompt = model.prompts[0]
    assert "439347004625" in prompt
    assert "TEST Feature request" in prompt


def test_a_focused_write_is_accepted(indexed):
    model = _Model(
        json.dumps(
            {
                "action": "tool",
                "tool": "HUBSPOT_UPDATE_TICKET",
                "args": {"id": "439347004625"},
            }
        )
    )
    out = _plan(
        model,
        {
            "user_id": "u1",
            "request": "set the priority to urgent",
            "connected_apps": ["hubspot"],
            "focus": FOCUS,
        },
    )
    assert out["tool_call"]["tool"] == "HUBSPOT_UPDATE_TICKET"
    assert out["tool_call"]["app"] == "hubspot"


# --- the approval ----------------------------------------------------------


def test_the_approval_names_the_record():
    """The user must be able to see which record a write would change."""
    from pa.graph.nodes import propose_action

    out = propose_action(
        {
            "tool_call": {
                "tool": "HUBSPOT_UPDATE_TICKET",
                "app": "hubspot",
                "args": {"id": "439347004625"},
                "classification": "write",
            },
            "focus": FOCUS,
        }
    )
    summary = out["proposed_action"]["summary"]
    assert "TEST Feature request" in summary
    assert "439347004625" in summary
    assert out["proposed_action"]["record_id"] == "439347004625"


def test_the_approval_without_a_focus_still_says_what_runs():
    from pa.graph.nodes import propose_action

    out = propose_action(
        {
            "tool_call": {
                "tool": "HUBSPOT_UPDATE_TICKET",
                "app": "hubspot",
                "args": {"id": "1"},
                "classification": "write",
            },
        }
    )
    assert "HUBSPOT_UPDATE_TICKET" in out["proposed_action"]["summary"]
