"""Unit tests: the assistant only ever talks about connected apps.

Regression cover for a real bug: the user asked for HubSpot records, and
the assistant answered about Zendesk — an app that was never connected. The
tool call was correctly rejected, but the *prose* was not checked, so the
model was free to describe data from an app it had never seen.
"""

import pytest


class _Msg:
    def __init__(self, content):
        self.content = content


class _ScriptedModel:
    """Returns each scripted answer in turn, repeating the last one."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts: list[str] = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return _Msg(answer)


@pytest.fixture()
def catalog_with_zendesk(monkeypatch):
    """A catalog containing Zendesk, so it can be named but not used."""
    from pa.integrations import catalog as catalog_mod

    apps = [
        {"slug": "hubspot", "name": "HubSpot", "description": "", "logo": ""},
        {"slug": "zendesk", "name": "Zendesk", "description": "", "logo": ""},
        {"slug": "gmail", "name": "Gmail", "description": "", "logo": ""},
    ]
    monkeypatch.setattr(catalog_mod, "list_apps", lambda limit=None, use_cache=True: list(apps))
    monkeypatch.setattr(catalog_mod, "_mention_cache", {"at": 0.0, "regex": None, "names": {}})
    return apps


# --- mention detection -----------------------------------------------------


def test_an_unconnected_app_named_in_prose_is_detected(catalog_with_zendesk):
    from pa.integrations import catalog as catalog_mod

    found = catalog_mod.mentioned_unconnected_apps(
        "Here are your Zendesk tickets.", ["hubspot", "gmail"]
    )
    assert found == ["zendesk"]


def test_connected_apps_are_not_flagged(catalog_with_zendesk):
    from pa.integrations import catalog as catalog_mod

    assert catalog_mod.mentioned_unconnected_apps("Your HubSpot deals look good.", ["hubspot"]) == []


def test_ordinary_prose_is_not_an_app_mention(catalog_with_zendesk):
    from pa.integrations import catalog as catalog_mod

    assert catalog_mod.mentioned_unconnected_apps("I will create a task for you.", ["hubspot"]) == []


# --- the guard -------------------------------------------------------------


def test_the_guard_flags_claiming_to_have_used_an_app(catalog_with_zendesk):
    from pa.services.app_context import guard_app_mentions

    violations = guard_app_mentions(
        "Here are the 3 Zendesk tickets you asked for.", ["hubspot", "gmail"]
    )
    assert violations == ["zendesk"]


def test_the_guard_allows_asking_the_user_to_connect(catalog_with_zendesk):
    from pa.services.app_context import guard_app_mentions

    text = "You're not connected to Zendesk yet. Open the Apps section to connect it."
    assert guard_app_mentions(text, ["hubspot", "gmail"]) == []


def test_the_guard_allows_a_plain_mention_when_nothing_is_claimed(catalog_with_zendesk):
    """A bare name with no claim and no 'connect' is still a violation."""
    from pa.services.app_context import guard_app_mentions

    assert guard_app_mentions("Zendesk has your tickets.", ["hubspot"]) == ["zendesk"]


# --- the response ----------------------------------------------------------


def _generate(model, state):
    from pa.graph.nodes import generate_response

    return generate_response(state, config={"configurable": {"model": model}})


def test_a_hallucinated_app_is_corrected(catalog_with_zendesk):
    """The reported bug: the answer talks about an unconnected Zendesk."""
    model = _ScriptedModel(
        [
            "Here are your 3 Zendesk tickets: #1, #2, #3.",
            "HubSpot has no tickets on file right now.",
        ]
    )
    out = _generate(
        model,
        {
            "user_id": "u1",
            "request": "show my tickets",
            "connected_apps": ["hubspot", "gmail"],
        },
    )
    assert "Zendesk" not in out["response"]
    assert "HubSpot" in out["response"]


def test_the_correction_is_asked_for_explicitly(catalog_with_zendesk):
    model = _ScriptedModel(["Here are your Zendesk tickets."])
    _generate(
        model,
        {"user_id": "u1", "request": "show my tickets", "connected_apps": ["hubspot"]},
    )
    assert len(model.prompts) == 2
    assert "CORRECTION" in model.prompts[1]
    assert "Zendesk" in model.prompts[1]


def test_an_answer_that_keeps_straying_is_not_shipped(catalog_with_zendesk):
    """Two violations in a row: do not present invented data."""
    model = _ScriptedModel(["Your Zendesk tickets are ready."])
    out = _generate(
        model,
        {"user_id": "u1", "request": "show my tickets", "connected_apps": ["hubspot"]},
    )
    assert "Zendesk" not in out["response"]
    assert "connected apps" in out["response"]


def test_a_clean_answer_is_left_alone(catalog_with_zendesk):
    model = _ScriptedModel(["Your HubSpot pipeline has 4 open deals."])
    out = _generate(
        model,
        {"user_id": "u1", "request": "how is my pipeline", "connected_apps": ["hubspot"]},
    )
    assert out["response"] == "Your HubSpot pipeline has 4 open deals."
    assert len(model.prompts) == 1


def test_asking_the_user_to_connect_is_not_corrected(catalog_with_zendesk):
    model = _ScriptedModel(
        ["You're not connected to Zendesk. Open the Apps section to connect it."]
    )
    out = _generate(
        model,
        {"user_id": "u1", "request": "show my zendesk tickets", "connected_apps": ["hubspot"]},
    )
    assert "Zendesk" in out["response"]
    assert len(model.prompts) == 1


# --- scope resolution ------------------------------------------------------


def test_naming_an_unconnected_app_resolves_as_unconnected(catalog_with_zendesk):
    from pa.services.app_context import resolve_app_scope

    scope = resolve_app_scope("show my zendesk tickets", [], ["hubspot", "gmail"])
    assert scope["unconnected"] == "zendesk"
    assert scope["target"] is None


def test_naming_a_connected_app_resolves_as_the_target(catalog_with_zendesk):
    from pa.services.app_context import resolve_app_scope

    scope = resolve_app_scope("show my hubspot tickets", [], ["hubspot", "gmail"])
    assert scope["target"] == "hubspot"
    assert scope["unconnected"] is None


def test_an_unconnected_app_gets_a_deterministic_answer(catalog_with_zendesk):
    """No model call: the answer is about connecting, not about data."""
    model = _ScriptedModel(["Here are your Zendesk tickets."])
    out = _generate(
        model,
        {
            "user_id": "u1",
            "request": "show my zendesk tickets",
            "connected_apps": ["hubspot", "gmail"],
            "unconnected_app": "zendesk",
        },
    )
    assert "not connected to Zendesk" in out["response"]
    assert model.prompts == []


def test_a_connected_target_still_answers_when_another_app_is_named(catalog_with_zendesk):
    model = _ScriptedModel(["Your HubSpot pipeline has 4 open deals."])
    out = _generate(
        model,
        {
            "user_id": "u1",
            "request": "compare hubspot and zendesk",
            "connected_apps": ["hubspot", "gmail"],
            "target_app": "hubspot",
            "unconnected_app": "zendesk",
        },
    )
    assert "HubSpot" in out["response"]
