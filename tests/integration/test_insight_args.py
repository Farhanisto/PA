"""The dashboard recipes must use arguments the tools actually accept.

``GMAIL_FETCH_EMAILS`` used ``maxResults`` and ``q``; the tool now takes
``max_results`` and ``query``, and the call failed with "Unknown
argument(s)". These tests check the recipes against the live tool schemas,
so a renamed parameter is caught here rather than on the dashboard.
"""

import pytest


@pytest.fixture()
def real_catalog():
    from pathlib import Path

    from pa.integrations import catalog, composio_client

    env_file = Path(".env")
    key = ""
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("COMPOSIO_API_KEY="):
                key = line.split("=", 1)[1].strip().strip("'\"")
    if not key:
        pytest.skip("no real Composio key is available")

    from pa.config import reset_settings_cache

    factory = composio_client._client_factory
    composio_client.clear_client_factory()
    composio_client.reset_client()
    import os

    os.environ["COMPOSIO_API_KEY"] = key
    reset_settings_cache()
    try:
        yield catalog
    finally:
        if factory is not None:
            composio_client.set_client_factory(factory)
        composio_client.reset_client()
        reset_settings_cache()


def _accepted_args(catalog, toolkit: str, tool: str) -> set[str]:
    """The argument names a tool accepts, from its live schema."""
    for t in catalog.list_tools(toolkit, limit=400, use_cache=False):
        if t["slug"] == tool:
            props = t.get("properties")
            if props is None:
                # The catalogue does not carry schemas; fetch them directly.
                from pa.integrations import composio_client

                client = composio_client.get_client()
                resp = client.client.tools.list(toolkit_slug=toolkit, limit=400)
                for item in getattr(resp, "items", None) or []:
                    if getattr(item, "slug", "") == tool:
                        params = getattr(item, "input_parameters", None) or {}
                        return set((params.get("properties") or {}).keys())
                return set()
            return set(props.keys())
    return set()


def _capture(recipe, payload: str) -> dict[str, dict]:
    """Run a recipe with a stub tool, and record the calls it makes."""
    captured: dict[str, dict] = {}

    def run(tool: str, args: dict) -> str:
        captured[tool] = args
        return payload

    recipe("u1", run)
    return captured


def test_the_gmail_recipe_uses_arguments_the_tool_accepts(real_catalog):
    from pa.services.app_insights import RECIPES

    captured = _capture(RECIPES["gmail"], '{"messages": []}')
    args = captured.get("GMAIL_FETCH_EMAILS", {})
    assert args, "the gmail recipe should pass some arguments"

    accepted = _accepted_args(real_catalog, "gmail", "GMAIL_FETCH_EMAILS")
    if not accepted:
        pytest.skip("the tool schema is not available")

    unknown = sorted(set(args) - accepted)
    assert not unknown, (
        "the gmail insight uses arguments GMAIL_FETCH_EMAILS does not accept: "
        + ", ".join(unknown)
        + f" (it accepts: {sorted(accepted)})"
    )


def test_the_hubspot_recipe_uses_arguments_the_tool_accepts(real_catalog):
    from pa.services.app_insights import RECIPES

    captured = _capture(RECIPES["hubspot"], '{"results": []}')
    args = captured.get("HUBSPOT_LIST_DEALS", {})
    assert args

    accepted = _accepted_args(real_catalog, "hubspot", "HUBSPOT_LIST_DEALS")
    if not accepted:
        pytest.skip("the tool schema is not available")

    unknown = sorted(set(args) - accepted)
    assert not unknown, (
        "the hubspot insight uses arguments HUBSPOT_LIST_DEALS does not accept: "
        + ", ".join(unknown)
        + f" (it accepts: {sorted(accepted)})"
    )


def test_the_gmail_recipe_reads_the_messages_envelope():
    """Gmail returns {"messages": [...]}, not {"results": [...]}."""
    from pa.services.app_insights import RECIPES

    captured = _capture(
        RECIPES["gmail"],
        '{"messages": [{"subject": "Hello", "sender": "a@example.com"}]}',
    )
    assert "GMAIL_FETCH_EMAILS" in captured

    summary = RECIPES["gmail"](
        "u1",
        lambda tool, args: '{"messages": [{"subject": "Hello", "sender": "a@example.com"}]}',
    )
    assert summary["headline"].startswith("1 message")
    assert summary["items"][0]["title"] == "Hello"
    assert summary["items"][0]["detail"] == "a@example.com"


def test_arguments_are_fitted_to_the_tool(real_catalog):
    """A renamed argument is dropped, not fatal."""
    from pa.services.tool_runner import fit_args

    meta = {"properties": {"max_results": {"type": "integer"}}}
    fitted = fit_args(meta, {"maxResults": 10, "max_results": 20})
    assert fitted == {"max_results": 20}


def test_a_tool_with_no_schema_keeps_its_arguments():
    from pa.services.tool_runner import fit_args

    assert fit_args({"properties": {}}, {"anything": 1}) == {"anything": 1}
    assert fit_args({}, {"anything": 1}) == {"anything": 1}
