"""Unit tests: pinning a request to the right app.

Regression cover for a real bug: a user asked for HubSpot records, answered
"tickets" to a clarifying question, and the assistant ran a Zendesk tool.
Two things had to go wrong — the answer lost its context, and the tool list
offered (and accepted) another app's tools.
"""

import json

import pytest


class _Msg:
    def __init__(self, content):
        self.content = content


class _Model:
    """A model that always answers with the same text."""

    def __init__(self, text):
        self.text = text
        self.prompts: list[str] = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return _Msg(self.text)


def _tool_call(slug: str) -> _Model:
    return _Model(json.dumps({"action": "tool", "tool": slug, "args": {}}))


# --- target app detection --------------------------------------------------


def test_request_naming_the_app_wins():
    from pa.services.app_context import detect_target_app

    assert detect_target_app("show me hubspot tickets", [], ["hubspot", "zendesk"]) == "hubspot"


def test_display_name_is_matched():
    from pa.services.app_context import detect_target_app

    assert detect_target_app("open google calendar", [], ["googlecalendar"]) == "googlecalendar"


def test_bare_answer_resolves_from_the_conversation():
    """The bug: "tickets" on its own, answered to a HubSpot question."""
    from pa.services.app_context import detect_target_app

    history = [
        {"role": "user", "content": "create new hubspot records"},
        {"role": "assistant", "content": "Which kind? contacts, companies, deals or tickets?"},
    ]
    assert detect_target_app("tickets", history, ["hubspot", "zendesk"]) == "hubspot"


def test_most_recent_mention_in_the_conversation_wins():
    from pa.services.app_context import detect_target_app

    history = [
        {"role": "user", "content": "check my hubspot deals"},
        {"role": "user", "content": "actually, look at slack instead"},
    ]
    assert detect_target_app("those messages", history, ["hubspot", "slack"]) == "slack"


def test_the_assistants_own_words_do_not_redirect_the_user():
    """PA mentioning another app must not capture the user's next message."""
    from pa.services.app_context import detect_target_app

    history = [
        {"role": "user", "content": "create new hubspot records"},
        {"role": "assistant", "content": "You could also do this in Zendesk."},
    ]
    assert detect_target_app("tickets", history, ["hubspot", "zendesk"]) == "hubspot"


def test_the_assistants_question_is_used_when_the_user_named_no_app():
    from pa.services.app_context import detect_target_app

    history = [
        {"role": "user", "content": "create new records"},
        {"role": "assistant", "content": "Should I use HubSpot for that?"},
    ]
    assert detect_target_app("tickets", history, ["hubspot", "zendesk"]) == "hubspot"


def test_no_app_mentioned_anywhere_is_ambiguous():
    from pa.services.app_context import detect_target_app

    assert detect_target_app("tickets", [], ["hubspot", "zendesk"]) is None


def test_two_apps_named_in_one_request_is_ambiguous():
    from pa.services.app_context import detect_target_app

    assert detect_target_app("compare hubspot and zendesk tickets", [], ["hubspot", "zendesk"]) is None


def test_detection_never_raises_without_apps():
    from pa.services.app_context import detect_target_app

    assert detect_target_app("tickets", [], []) is None


def test_common_word_slugs_do_not_match_bare_text():
    """'notion' must not match 'I have a notion of...'."""
    from pa.services.app_context import detect_target_app

    assert detect_target_app("I have a notion of what you mean", [], ["notion"]) is None


# --- tool selection --------------------------------------------------------

_INDEX = {
    "HUBSPOT_LIST_TICKETS": {
        "app": "hubspot",
        "description": "List tickets in HubSpot",
        "classification": "read",
        "required": [],
        "properties": {},
    },
    "HUBSPOT_CREATE_TICKET": {
        "app": "hubspot",
        "description": "Create a ticket in HubSpot",
        "classification": "write",
        "required": [],
        "properties": {},
    },
    "ZENDESK_LIST_TICKETS": {
        "app": "zendesk",
        "description": "List tickets in Zendesk",
        "classification": "read",
        "required": [],
        "properties": {},
    },
    "ZENDESK_CREATE_TICKET": {
        "app": "zendesk",
        "description": "Create a ticket in Zendesk",
        "classification": "write",
        "required": [],
        "properties": {},
    },
}


@pytest.fixture()
def indexed(monkeypatch):
    from pa.services import tool_runner

    monkeypatch.setattr(tool_runner, "tool_index", lambda user_id: dict(_INDEX))


def _plan(model, state):
    from pa.graph.nodes import plan_action

    return plan_action(state, config={"configurable": {"model": model}})


def test_a_tool_from_another_app_is_rejected(indexed):
    """The dangerous half: the model named Zendesk for a HubSpot request."""
    state = {
        "user_id": "u1",
        "request": "list hubspot tickets",
        "connected_apps": ["hubspot", "zendesk"],
    }
    out = _plan(_tool_call("ZENDESK_LIST_TICKETS"), state)
    assert "tool_call" not in out, out


def test_the_right_apps_tool_is_accepted(indexed):
    state = {
        "user_id": "u1",
        "request": "list hubspot tickets",
        "connected_apps": ["hubspot", "zendesk"],
    }
    out = _plan(_tool_call("HUBSPOT_LIST_TICKETS"), state)
    assert out["tool_call"]["tool"] == "HUBSPOT_LIST_TICKETS"
    assert out["tool_call"]["app"] == "hubspot"


def test_a_bare_answer_still_pins_to_the_conversation_app(indexed):
    state = {
        "user_id": "u1",
        "request": "tickets",
        "connected_apps": ["hubspot", "zendesk"],
        "history": [
            {"role": "user", "content": "create new hubspot records"},
            {"role": "assistant", "content": "Which kind?"},
        ],
    }
    out = _plan(_tool_call("ZENDESK_LIST_TICKETS"), state)
    assert "tool_call" not in out, out

    out = _plan(_tool_call("HUBSPOT_LIST_TICKETS"), state)
    assert out["tool_call"]["app"] == "hubspot"


def test_the_prompt_tells_the_model_which_app_to_use(indexed):
    model = _tool_call("HUBSPOT_LIST_TICKETS")
    _plan(
        model,
        {
            "user_id": "u1",
            "request": "list hubspot tickets",
            "connected_apps": ["hubspot", "zendesk"],
        },
    )
    prompt = model.prompts[0]
    assert "hubspot" in prompt.lower()
    assert "Never use another app's tool" in prompt


def test_only_the_target_apps_tools_are_listed(indexed):
    model = _tool_call("HUBSPOT_LIST_TICKETS")
    _plan(
        model,
        {
            "user_id": "u1",
            "request": "list hubspot tickets",
            "connected_apps": ["hubspot", "zendesk"],
        },
    )
    listed = model.prompts[0]
    assert "HUBSPOT_LIST_TICKETS" in listed
    assert "ZENDESK_LIST_TICKETS" not in listed


def test_with_no_target_app_all_tools_remain_available(indexed):
    model = _tool_call("ZENDESK_LIST_TICKETS")
    out = _plan(
        model,
        {"user_id": "u1", "request": "tickets", "connected_apps": ["hubspot", "zendesk"]},
    )
    assert out["tool_call"]["app"] == "zendesk"


def test_an_ambiguous_request_leaves_the_choice_to_the_model(indexed):
    model = _tool_call("ZENDESK_LIST_TICKETS")
    out = _plan(
        model,
        {
            "user_id": "u1",
            "request": "compare hubspot and zendesk tickets",
            "connected_apps": ["hubspot", "zendesk"],
        },
    )
    assert out["tool_call"]["tool"] == "ZENDESK_LIST_TICKETS"


def test_no_connected_apps_is_not_an_error(indexed):
    from pa.graph.nodes import plan_action

    out = plan_action(
        {"user_id": "u1", "request": "tickets", "connected_apps": []},
        config={"configurable": {"model": _Model("hello")}},
    )
    assert out.get("approval_status") == "not_required"


# --- the prompt carries the conversation ------------------------------------


def test_the_prompt_includes_earlier_turns():
    from pa.graph.nodes import _prompt_for

    prompt = _prompt_for(
        {
            "request": "tickets",
            "history": [
                {"role": "user", "content": "create new hubspot records"},
                {"role": "assistant", "content": "Which kind?"},
            ],
        }
    )
    assert "create new hubspot records" in prompt
    assert "Conversation so far" in prompt


def test_the_prompt_works_without_history():
    from pa.graph.nodes import _prompt_for

    prompt = _prompt_for({"request": "list my deals"})
    assert "list my deals" in prompt
