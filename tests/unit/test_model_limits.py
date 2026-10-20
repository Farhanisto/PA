"""Unit tests: prompt size, tool ranking, and model failure handling.

Regression cover for a real outage: the tool-selection prompt grew past the
provider's per-minute token budget and every assistant turn failed with
``model_error: APIStatusError`` (HTTP 413).
"""

import pytest


# --- prompt size -----------------------------------------------------------


def _hubspot_like_tools(count: int) -> dict:
    index = {}
    for i in range(count):
        index[f"HUBSPOT_TOOL_{i:03d}"] = {
            "app": "hubspot",
            "description": (
                "Create, update, list or manage HubSpot CRM records such as "
                "contacts, companies, deals, tickets and pipelines. " * 2
            ),
        }
    return index


def test_the_tool_list_stays_within_its_budget():
    from pa.graph.nodes import MAX_TOOL_LIST_CHARS, MAX_TOOLS_SHOWN, _tool_list

    index = _hubspot_like_tools(500)
    listed = _tool_list(index, ["tickets"], MAX_TOOLS_SHOWN)
    assert len(listed) <= MAX_TOOL_LIST_CHARS + 200


def test_a_huge_toolkit_does_not_produce_a_huge_prompt():
    """The prompt must stay small no matter how many tools the app has."""
    from pa.graph.nodes import MAX_TOOLS_SHOWN, _tool_list

    index = _hubspot_like_tools(2000)
    listed = _tool_list(index, ["tickets"], MAX_TOOLS_SHOWN)
    # ~4 chars per token is a rough estimate; the budget is in characters.
    assert len(listed) < 12000


def test_the_tool_count_is_capped():
    from pa.graph.nodes import MAX_TOOLS_SHOWN, _tool_list

    index = _hubspot_like_tools(500)
    lines = _tool_list(index, [], MAX_TOOLS_SHOWN).split("\n")
    assert len(lines) <= MAX_TOOLS_SHOWN


# --- ranking ---------------------------------------------------------------


def test_the_matching_tool_ranks_first():
    from pa.graph.nodes import _rank_tools

    index = {
        "HUBSPOT_LIST_TICKETS": {"app": "hubspot", "description": "List tickets"},
        "HUBSPOT_LIST_DEALS": {"app": "hubspot", "description": "List deals"},
    }
    tokens = ["show", "hubspot", "tickets"]
    assert _rank_tools("HUBSPOT_LIST_TICKETS", index["HUBSPOT_LIST_TICKETS"], tokens) > _rank_tools(
        "HUBSPOT_LIST_DEALS", index["HUBSPOT_LIST_DEALS"], tokens
    )


def test_a_plural_request_matches_a_singular_slug():
    """Users say "tickets"; slugs are not consistent about the plural."""
    from pa.graph.nodes import _rank_tools

    meta = {"app": "hubspot", "description": "List ticket records"}
    singular = _rank_tools("HUBSPOT_LIST_TICKET", meta, ["tickets"])
    unrelated = _rank_tools("HUBSPOT_LIST_DEALS", meta, ["tickets"])
    assert singular > unrelated


def test_the_right_tool_is_in_the_shown_list():
    from pa.graph.nodes import MAX_TOOLS_SHOWN, _tool_list

    index = {
        "HUBSPOT_LIST_TICKETS": {"app": "hubspot", "description": "List tickets"},
        "HUBSPOT_GET_TICKET": {"app": "hubspot", "description": "Get a ticket"},
    }
    for n in range(400):
        index[f"HUBSPOT_HELPER_{n:03d}"] = {
            "app": "hubspot",
            "description": "Additional HubSpot helper for records and pipelines.",
        }
    listed = _tool_list(index, ["hubspot", "tickets"], MAX_TOOLS_SHOWN)
    assert "HUBSPOT_LIST_TICKETS" in listed


# --- failure handling ------------------------------------------------------


class _FakeStatusError(Exception):
    """Shaped like the openai SDK's APIStatusError."""

    def __init__(self, status: int, message: str = "boom"):
        super().__init__(f"Error code: {status} - {message}")
        self.status_code = status


def test_the_status_is_read_from_the_exception():
    from pa.models.errors import status_of

    assert status_of(_FakeStatusError(413)) == 413
    assert status_of(_FakeStatusError(429)) == 429


def test_the_status_is_read_from_a_serialised_error():
    """The error travels through the graph as a string."""
    from pa.models.errors import status_of

    assert status_of(Exception("model_error: APIStatusError: status=413: too big")) == 413


def test_a_token_budget_failure_is_transient():
    from pa.models.errors import is_transient

    assert is_transient(_FakeStatusError(413))
    assert is_transient(_FakeStatusError(429))
    assert is_transient(_FakeStatusError(503))


def test_a_bad_request_is_not_transient():
    from pa.models.errors import is_transient

    assert not is_transient(_FakeStatusError(400))
    assert not is_transient(_FakeStatusError(401))


def test_the_error_keeps_its_status():
    from pa.models.errors import describe

    described = describe(_FakeStatusError(413, "too large"))
    assert "status=413" in described
    assert "_FakeStatusError" in described


def test_the_user_gets_an_actionable_message():
    from pa.models.errors import friendly

    assert "rate limiting" in friendly(_FakeStatusError(429)).lower()
    assert "API key" in friendly(_FakeStatusError(401))
    assert "too large" in friendly(_FakeStatusError(413)).lower()


def test_a_token_budget_failure_waits_for_the_window():
    """Retrying in two seconds just fails again."""
    from pa.queue.tasks import _retry_countdown

    assert _retry_countdown(Exception("status=413"), 0) >= 30
    assert _retry_countdown(Exception("status=429"), 0) >= 30
    assert _retry_countdown(Exception("status=500"), 0) < 30


def test_the_job_error_is_actionable_not_raw():
    from pa.queue.tasks import _friendly_error_code

    raw = "model_error: APIStatusError: status=429: Error code: 429 - {'error': ...}"
    friendly = _friendly_error_code(raw)
    assert "rate limiting" in friendly.lower()
    assert "APIStatusError" not in friendly


def test_a_non_model_error_keeps_its_text():
    from pa.queue.tasks import _friendly_error_code

    assert _friendly_error_code("tool_lookup_error: KeyError") == "tool_lookup_error: KeyError"


# --- the cost of a turn ----------------------------------------------------


def test_a_tool_run_does_not_spend_a_model_call_on_prose():
    """A tool's summary replaces the prose, so the prose is not written."""
    from pa.graph import builder as b
    from pa.services import tool_runner

    index = {
        "ACME_LIST_DEALS": {
            "app": "acme",
            "description": "List deals",
            "classification": "read",
            "required": [],
            "properties": {},
        }
    }
    tool_runner.tool_index = lambda user_id: dict(index)

    class _Msg:
        content = '{"action":"tool","tool":"ACME_LIST_DEALS","args":{}}'

    class _Model:
        def __init__(self):
            self.calls = 0

        def invoke(self, prompt):
            self.calls += 1
            return _Msg()

    model = _Model()
    b.after_plan({"tool_call": {"classification": "read"}})
    assert b.after_plan({"tool_call": {"classification": "read"}}) == "run_read_tool"

    # The prose node is only reached when no read tool ran.
    from pa.graph.nodes import generate_response

    out = generate_response(
        {"request": "hi", "connected_apps": ["acme"]},
        config={"configurable": {"model": model}},
    )
    assert model.calls == 1
    assert "response" in out


def test_the_prompt_stays_within_its_budget():
    from pa.graph.nodes import MAX_PROMPT_CHARS, _prompt_for

    prompt = _prompt_for(
        {
            "request": "tickets",
            "app_context": "apps: " + ("x" * 20_000),
            "history": [{"role": "user", "content": "y" * 5000} for _ in range(20)],
        }
    )
    assert len(prompt) <= MAX_PROMPT_CHARS


def test_the_request_survives_a_trim():
    """The rules and the request are the last things to go."""
    from pa.graph.nodes import _prompt_for

    prompt = _prompt_for(
        {
            "request": "show my tickets",
            "app_context": "apps: " + ("x" * 20_000),
            "history": [{"role": "user", "content": "y" * 5000} for _ in range(20)],
        }
    )
    assert "show my tickets" in prompt
    assert "Never invent app data" in prompt


def test_the_tool_prompt_stays_within_its_budget():
    from pa.graph.nodes import MAX_PROMPT_CHARS, plan_action
    from pa.services import tool_runner

    index = {
        f"ACME_TOOL_{i:03d}": {
            "app": "acme",
            "description": "Do a thing with a record. " * 10,
            "classification": "read",
            "required": [],
            "properties": {},
        }
        for i in range(500)
    }
    tool_runner.tool_index = lambda user_id: dict(index)

    class _Msg:
        content = '{"action":"reply","text":"ok"}'

    class _Model:
        def __init__(self):
            self.prompts = []

        def invoke(self, prompt, **_kw):
            self.prompts.append(prompt)
            return _Msg()

    model = _Model()
    plan_action(
        {
            "user_id": "u1",
            "request": "show my deals",
            "target_app": "acme",
            "connected_apps": ["acme"],
        },
        config={"configurable": {"model": model}},
    )
    assert len(model.prompts[0]) <= MAX_PROMPT_CHARS
    assert "show my deals" in model.prompts[0]


# --- migration failures ----------------------------------------------------


def test_a_database_ahead_of_the_build_says_to_rebuild():
    """The database was migrated by a newer image; only a rebuild fixes it."""
    from pa.db.database import _migration_error

    exc = Exception("Can't locate revision identified by 'a1b2c3d4e5f6'")
    message = _migration_error(exc)
    assert "Rebuild" in message
    assert "docker compose" in message
    assert "a1b2c3d4e5f6" in message


def test_other_migration_failures_keep_their_detail():
    from pa.db.database import _migration_error

    exc = Exception("relation \"jobs\" already exists")
    message = _migration_error(exc)
    assert "already exists" in message
