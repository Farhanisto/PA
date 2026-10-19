"""Unit tests: config, model adapter, graph routing, policy, memory, retry."""

import json

import pytest


def test_config_missing_keys_raise_clearly(monkeypatch):
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    monkeypatch.delenv("NEBIUS_MODEL", raising=False)
    from pa.models.nebius import create_nebius_model

    with pytest.raises(RuntimeError, match="NEBIUS_API_KEY"):
        create_nebius_model()


def test_config_missing_model_raises(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "k")
    monkeypatch.delenv("NEBIUS_MODEL", raising=False)
    from pa.models.nebius import create_nebius_model

    with pytest.raises(RuntimeError, match="NEBIUS_MODEL"):
        create_nebius_model()


def test_model_adapter_success(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "k")
    monkeypatch.setenv("NEBIUS_MODEL", "m")
    from pa.models.nebius import create_nebius_model

    model = create_nebius_model()
    assert model is not None


def test_groq_missing_key_raises(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    from pa.models.groq import create_groq_model

    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        create_groq_model()


def test_groq_adapter_defaults(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    from pa.models.groq import create_groq_model

    model = create_groq_model()
    assert model.model_name == "openai/gpt-oss-120b"
    assert "groq" in str(model.openai_api_base)


def test_factory_uses_groq_when_key_set(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("NEBIUS_API_KEY", "k2")
    monkeypatch.setenv("NEBIUS_MODEL", "m2")
    from pa.models.factory import create_model, resolve_provider

    assert resolve_provider() == "groq"
    model = create_model()
    assert "groq" in str(model.openai_api_base)


def test_factory_falls_back_to_nebius(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("NEBIUS_API_KEY", "k")
    monkeypatch.setenv("NEBIUS_MODEL", "m")
    from pa.models.factory import create_model, resolve_provider

    assert resolve_provider() == "nebius"
    assert create_model() is not None


def test_factory_no_keys_raises_nebius_error(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    monkeypatch.delenv("NEBIUS_MODEL", raising=False)
    from pa.models.factory import create_model

    with pytest.raises(RuntimeError, match="NEBIUS_API_KEY"):
        create_model()


def test_graph_routes_to_expected_nodes(fake_model):
    from pa.graph.builder import build_graph

    graph = build_graph()
    # Compile succeeded and nodes registered.
    nodes = set(graph.get_graph().nodes.keys())
    for expected in (
        "understand_request",
        "prepare_context",
        "generate_response",
        "verify_result",
        "propose_action",
        "approval_gate",
        "finalize",
    ):
        assert expected in nodes


def test_understand_request_classification():
    from pa.graph.nodes import understand_request

    assert understand_request({"request": "Prepare morning briefing"})["workflow"] == "briefing"
    assert understand_request({"request": "Please send an email to bob"})["workflow"] == "action"
    assert understand_request({"request": "What is 2+2?"})["workflow"] == "general"


def test_generate_response_uses_fake_model(fake_model):
    from pa.graph.nodes import generate_response

    out = generate_response({"request": "hi"}, config={"configurable": {"model": fake_model}})
    assert "response" in out
    assert "fake" in out["response"].lower()


def test_policy_denies_unauthorized_tools():
    from pa.security.policy import is_action_allowed

    assert not is_action_allowed(tool_name="rm_rf", user_id="u", args={})
    assert not is_action_allowed(tool_name="compose_email", user_id="", args={})
    assert is_action_allowed(tool_name="compose_email", user_id="u", args={"to": "a@b.co"})


def test_invalid_tool_args_rejected():
    from pa.tools.registry import invoke_tool

    with pytest.raises(ValueError, match="not allowed"):
        invoke_tool("nonexistent_tool", {})
    with pytest.raises(ValueError, match="recipient"):
        invoke_tool("compose_email", {"to": "not-an-email", "body": "hi"})


def test_memory_scoped_and_deletable(db_session):
    from pa.memory import service as mem

    m1 = mem.save_memory(db_session, user_id="alice", content="likes tea", category="preference")
    mem.save_memory(db_session, user_id="bob", content="likes coffee")
    alice = mem.list_memories(db_session, user_id="alice")
    assert [m.id for m in alice] == [m1.id]
    mem.delete_memory(db_session, user_id="alice", memory_id=m1.id)
    assert mem.list_memories(db_session, user_id="alice") == []


def test_memory_rejects_secrets(db_session):
    from pa.memory import service as mem

    with pytest.raises(ValueError, match="secret|credential"):
        mem.save_memory(db_session, user_id="alice", content="my api_key is XYZ")


def test_retry_policy_distinguishes():
    from pa.queue.tasks import is_transient_error

    assert is_transient_error(ConnectionError("connection reset"))
    assert is_transient_error(Exception("provider timeout after 60s"))
    assert is_transient_error(Exception("503 Service Unavailable"))
    assert not is_transient_error(ValueError("invalid request"))
    assert not is_transient_error(PermissionError("not authorized"))


def test_sending_tools_require_approval():
    from pa.security.policy import requires_approval

    assert requires_approval("email_send_draft")


# --- Composio toolkit slugs: any catalog toolkit is connectable ---


def test_any_catalog_toolkit_slug_is_accepted():
    """No fixed whitelist: Composio serves many toolkits, all must work."""
    from pa.integrations.composio_client import toolkit_slug

    for name in [
        "gmail",
        "notion",
        "slack",
        "github",
        "googlecalendar",
        "outlook",
        "hubspot",
        "airtable",
        "apollo",
        "zendesk",
        "salesforce",
        "microsoft_teams",
        "some-brand-new-toolkit",
    ]:
        assert toolkit_slug(name) == name.replace("_", "-").lower()


def test_toolkit_slug_normalises_input():
    from pa.integrations.composio_client import toolkit_slug

    assert toolkit_slug("  Gmail  ") == "gmail"
    assert toolkit_slug("Microsoft_Teams") == "microsoft-teams"
    assert toolkit_slug("google calendar") == "google-calendar"


def test_toolkit_slug_rejects_junk():
    from pa.integrations.composio_client import toolkit_slug

    for bad in ["", "   ", "../etc/passwd", "toolkit;drop", "a" * 200]:
        with pytest.raises(ValueError):
            toolkit_slug(bad)


@pytest.mark.parametrize("provider", ["gmail", "notion", "slack", "github", "zendesk"])
def test_connect_works_for_any_toolkit(provider, fake_composio):
    """Previously only gmail/whatsapp/zoom worked; now any catalog slug does."""
    from pa.integrations import composio_client as cc

    out = cc.initiate_connection("u1", provider)
    assert out["provider"] == provider
    assert out["redirect_url"].startswith("https://")


def test_connect_reports_missing_auth_config_clearly(fake_composio):
    """With no auth config available the error must say so, not 'unknown provider'."""
    from pa.integrations import composio_client as cc

    fake_composio.client.auth_configs.list = lambda **_k: type("R", (), {"items": []})()
    with pytest.raises(ValueError) as exc:
        cc.initiate_connection("u1", "notion")
    msg = str(exc.value)
    assert "unknown provider" not in msg
    assert "auth config" in msg


# --- Composio connection-state fan-out: one call, not one per toolkit ---


def test_connection_states_uses_a_single_batched_call(fake_composio):
    """60 toolkits must not mean 60 network round trips."""
    from pa.integrations import composio_client as cc

    cc.invalidate_states()
    calls = []
    real = fake_composio.client.connected_accounts.list

    def counting(**kwargs):
        calls.append(kwargs)
        return real(**kwargs)

    fake_composio.client.connected_accounts.list = counting

    states = cc.connection_states("u1")
    assert len(calls) == 1, f"expected 1 batched call, made {len(calls)}"
    # No per-toolkit filter: that was the N+1.
    assert "toolkit_slugs" not in calls[0]
    # The double exposes one active gmail account and nothing else.
    assert states == {"gmail": "active"}


def test_connection_states_are_cached_then_invalidated(fake_composio):
    from pa.integrations import composio_client as cc

    cc.invalidate_states()
    calls = []
    real = fake_composio.client.connected_accounts.list

    def counting(**kwargs):
        calls.append(kwargs)
        return real(**kwargs)

    fake_composio.client.connected_accounts.list = counting

    cc.connection_states("u1")
    cc.connection_states("u1")
    cc.connection_states("u1")
    assert len(calls) == 1, "repeat reads should hit the cache"

    # A connect must invalidate immediately, or the UI shows a stale state.
    cc.initiate_connection("u1", "gmail")
    cc.connection_states("u1")
    assert len(calls) == 2, "connect must invalidate the cached states"


def test_batched_states_map_toolkit_slugs(fake_composio):
    """Toolkit slug and status are read correctly off the account shape."""
    from pa.integrations import composio_client as cc

    class Account:
        def __init__(self, slug, status):
            self.toolkit = type("T", (), {"slug": slug})()
            self.status = status
            self.id = f"ca-{slug}"

    class Resp:
        items = [
            Account("gmail", "ACTIVE"),
            Account("slack", "INITIALIZING"),
            Account("notion", "FAILED"),
            Account("github", "EXPIRED"),
        ]

    fake_composio.client.connected_accounts.list = lambda **k: Resp()
    cc.invalidate_states()
    states = cc.connection_states("u1")
    assert states["gmail"] == "active"
    assert states["slack"] == "pending"
    assert states["notion"] == "pending"
    assert states["github"] == "pending"


def test_catalog_is_cached(fake_composio):
    """The toolkit list is near-static; repeated calls must not re-fetch."""
    from pa.integrations import catalog

    catalog._catalog_cache["at"] = 0.0
    catalog._catalog_cache["apps"] = None
    calls = []
    real = fake_composio.client.toolkits.list

    def counting(**kwargs):
        calls.append(kwargs)
        return real(**kwargs)

    fake_composio.client.toolkits.list = counting

    catalog.list_apps()
    catalog.list_apps()
    catalog.list_apps()
    assert len(calls) == 1, "catalog should be served from cache"
    # A forced refresh must bypass it.
    catalog.list_apps(use_cache=False)
    assert len(calls) == 2


def test_composio_client_is_memoised(fake_composio):
    """Composio(...) costs seconds to build; it must not be rebuilt per call."""
    from pa.integrations import composio_client as cc

    cc.reset_client()
    built = []

    class Slow:
        def __init__(self):
            built.append(1)

    # Bypass the test factory so the real construction path is exercised.
    cc.clear_client_factory()
    try:
        import composio

        real = composio.Composio
        composio.Composio = lambda **_kw: Slow()
        try:
            cc.get_client()
            cc.get_client()
            cc.get_client()
            assert len(built) == 1, "client was rebuilt instead of reused"
        finally:
            composio.Composio = real
    finally:
        cc.reset_client()


def test_catalog_walks_cursor_pagination(fake_composio):
    """The catalog must not be truncated to a single Composio page."""
    from pa.integrations import catalog

    catalog._catalog_cache["at"] = 0.0
    catalog._catalog_cache["apps"] = None

    pages = [
        type("R", (), {"items": [{"slug": f"app{i}", "name": f"App {i}"} for i in range(0, 5)],
                       "next_cursor": "c1"})(),
        type("R", (), {"items": [{"slug": f"app{i}", "name": f"App {i}"} for i in range(5, 9)],
                       "next_cursor": None})(),
    ]
    seen_cursors = []

    def fake_list(**kw):
        seen_cursors.append(kw.get("cursor"))
        return pages[min(len(seen_cursors) - 1, len(pages) - 1)]

    fake_composio.client.toolkits.list = fake_list
    apps = catalog.list_apps(use_cache=False)
    assert len(apps) == 9, f"expected all pages, got {len(apps)}"
    assert seen_cursors[0] is None
    assert seen_cursors[1] == "c1", "second page must follow the cursor"


def test_catalog_dedupes_across_pages(fake_composio):
    from pa.integrations import catalog

    catalog._catalog_cache["at"] = 0.0
    catalog._catalog_cache["apps"] = None
    pages = [
        type("R", (), {"items": [{"slug": "gmail", "name": "Gmail"}], "next_cursor": "c1"})(),
        type("R", (), {"items": [{"slug": "gmail", "name": "Gmail"}], "next_cursor": None})(),
    ]
    n = {"i": 0}

    def fake_list(**_kw):
        r = pages[min(n["i"], 1)]
        n["i"] += 1
        return r

    fake_composio.client.toolkits.list = fake_list
    assert len(catalog.list_apps(use_cache=False)) == 1


def test_list_tools_returns_normalised_tools(fake_composio):
    from pa.integrations import catalog

    catalog._tool_cache.clear()

    class Tool:
        def __init__(self, slug):
            self.slug = slug
            self.name = slug.replace("_", " ").title()
            self.description = "does a thing"

    fake_composio.client.tools.list = lambda **k: type(
        "R", (), {"items": [Tool("GMAIL_SEND_EMAIL"), Tool("GMAIL_FETCH_EMAILS")]}
    )()
    tools = catalog.list_tools("gmail")
    assert [t["slug"] for t in tools] == ["GMAIL_SEND_EMAIL", "GMAIL_FETCH_EMAILS"]
    assert tools[0]["description"] == "does a thing"


def test_tool_execution_passes_the_user_ref(fake_composio):
    """Composio 400s a connected_account_id without a user_id; verify both are sent."""
    from pa.integrations import gmail_composio

    msgs = gmail_composio.fetch_inbox("user-123")
    assert len(msgs) == 3
    assert fake_composio.executed, "no tool call recorded"
    call = fake_composio.executed[-1]
    assert call["user_id"] == "user-123"
    assert call["account"] == "ca-1"
    assert call["tool"] == "GMAIL_FETCH_EMAILS"


def test_tool_execution_send_passes_the_user_ref(fake_composio):
    from pa.integrations import gmail_composio

    gmail_composio.send_email("user-123", to="a@b.com", subject="hi", body="hello")
    call = [c for c in fake_composio.executed if c["tool"] == "GMAIL_SEND_EMAIL"][-1]
    assert call["user_id"] == "user-123"


# --- Per-user proactive schedule ---


class _Sched:
    def __init__(self, bh=8, bm=0, fh=16, fm=0, be=True, fe=True, tz="UTC", uid="u1"):
        self.user_id = uid
        self.briefing_enabled, self.briefing_hour, self.briefing_minute = be, bh, bm
        self.followup_enabled, self.followup_hour, self.followup_minute = fe, fh, fm
        self.timezone = tz


def _now(h, m):
    from datetime import datetime

    return datetime(2026, 1, 1, h, m)


def test_is_due_fires_once_inside_the_window():
    from pa.queue.tasks import _is_due

    s = _Sched(bh=8, bm=30)
    assert _is_due(s, which="briefing", now_local=_now(8, 30)) is True
    assert _is_due(s, which="briefing", now_local=_now(8, 44)) is True
    # Past the 15-minute window: already handled.
    assert _is_due(s, which="briefing", now_local=_now(8, 45)) is False
    assert _is_due(s, which="briefing", now_local=_now(9, 10)) is False


def test_is_due_respects_disabled_switches():
    from pa.queue.tasks import _is_due

    off = _Sched(bh=8, be=False, fe=False, fh=16)
    assert _is_due(off, which="briefing", now_local=_now(8, 0)) is False
    assert _is_due(off, which="followup", now_local=_now(16, 0)) is False


def test_repositories_use_only_real_session_methods():
    """A typo like db.scalates(...) only surfaces at runtime; catch it statically."""
    import ast
    import pathlib as _pl

    from sqlalchemy.orm import Session

    valid = {m for m in dir(Session) if not m.startswith("_")}

    import pa.db.repositories as repo_mod

    for path in _pl.Path(repo_mod.__file__).parent.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            recv = node.func.value
            # `db.method(...)` parses as Attribute(value=Name("db")).
            is_db = (isinstance(recv, ast.Name) and recv.id == "db") or (
                isinstance(recv, ast.Attribute) and recv.attr == "db"
            )
            if not is_db:
                continue
            method = node.func.attr
            if method.startswith("_"):
                continue
            assert method in valid, f"{path.name}: db.{method}(...) is not a Session method"


# --- Assistant is app-aware, not a generic chatbot ---


def test_prompt_includes_connected_apps_and_stays_scoped():
    from pa.graph.nodes import _prompt_for

    prompt = _prompt_for(
        {
            "request": "What is 2+2?",
            "app_context": "The user has these apps connected: hubspot, gmail",
        }
    )
    assert "hubspot" in prompt
    assert "acts only through the user's connected apps" in prompt
    assert "Never invent app data" in prompt
    assert "Never ask the user for a password, API key" in prompt


def test_prompt_without_app_context_still_scopes():
    from pa.graph.nodes import _prompt_for

    prompt = _prompt_for({"request": "hello"})
    assert "acts only through the user's connected apps" in prompt
    assert "Do not answer general knowledge" in prompt


def test_app_context_reports_no_apps_when_nothing_connected(fake_composio):
    from pa.services.app_context import connected_apps_context

    fake_composio.connected = False  # no ACTIVE account
    ctx = connected_apps_context("u1")
    assert ctx["apps"] == []
    assert "NOT connected any apps" in ctx["text"]


def test_app_context_lists_active_apps_and_their_tools(fake_composio):
    from pa.services.app_context import connected_apps_context

    fake_composio.connected = True
    ctx = connected_apps_context("u1")
    assert ctx["apps"] == ["gmail"]
    assert "gmail" in ctx["text"]
    assert "GMAIL_FETCH_EMAILS" in ctx["text"]
    # Scoping instruction must always be present.
    assert "Answer only about work these connected apps" in ctx["text"]


def test_app_context_distinguishes_unreachable_from_unconnected(fake_composio):
    """"No apps" and "cannot check" are different answers to the model."""
    from pa.services.app_context import connected_apps_context

    def boom(**_k):
        raise RuntimeError("composio down")

    fake_composio.client.connected_accounts.list = boom
    ctx = connected_apps_context("u1")
    assert ctx["apps"] == []
    assert "unavailable" in ctx["text"]
    assert "Do not assume the user has no apps" in ctx["text"]


def test_connection_states_returns_none_when_unreachable(fake_composio):
    from pa.integrations import composio_client as cc

    def boom(**_k):
        raise RuntimeError("down")

    fake_composio.client.connected_accounts.list = boom
    cc.invalidate_states()
    assert cc.connection_states("u1") is None
    cc.invalidate_states()


def test_connection_state_says_unavailable_not_none_when_down(fake_composio):
    from pa.integrations import composio_client as cc

    def boom(**_k):
        raise RuntimeError("down")

    fake_composio.client.connected_accounts.list = boom
    cc.invalidate_states()
    assert cc.connection_state("u1", "gmail") == "unavailable"
    cc.invalidate_states()


def test_app_context_is_per_user(fake_composio):
    """User A's connections must never leak into user B's context."""
    from pa.services.app_context import connected_apps_context

    seen = []

    def listing(**kw):
        seen.append(kw.get("user_ids"))
        return type("R", (), {"items": []})()

    fake_composio.client.connected_accounts.list = listing
    connected_apps_context("alice")
    connected_apps_context("bob")
    assert seen == [["alice"], ["bob"]]


# --- Composio tool execution safety ---


def test_classify_reads_vs_writes():
    from pa.services.tool_runner import classify

    for read in ("HUBSPOT_LIST_DEALS", "GMAIL_FETCH_EMAILS", "SALESFORCE_QUERY",
                 "SLACK_LIST_CONVERSATIONS", "HUBSPOT_GET_CONTACT_BY_ID"):
        assert classify(read) == "read", read
    for write in ("HUBSPOT_CREATE_DEAL", "HUBSPOT_UPDATE_CONTACT", "GMAIL_SEND_EMAIL",
                  "HUBSPOT_ARCHIVE_COMPANY", "SALESFORCE_CONVERT_LEAD",
                  "HUBSPOT_DELETE_CONTACT"):
        assert classify(write) == "write", write


def test_unknown_tool_defaults_to_write():
    """Anything unrecognised must be gated, never auto-run."""
    from pa.services.tool_runner import classify

    assert classify("SOME_FUTURE_TOOL_THAT_WE_DO_NOT_KNOW") == "write"
    assert classify("") == "write"


def test_validate_rejects_tool_not_in_index():
    from pa.services.tool_runner import ToolError, validate

    with pytest.raises(ToolError) as e:
        validate({}, "HUBSPOT_CREATE_DEAL", {})
    assert "not available" in str(e.value)


def test_validate_rejects_unknown_and_missing_arguments():
    from pa.services.tool_runner import ToolError, validate

    index = {
        "HUBSPOT_CREATE_DEAL": {
            "app": "hubspot",
            "required": ["dealname"],
            "properties": {"dealname": {"type": "string"}, "amount": {"type": "string"}},
            "classification": "write",
        }
    }
    with pytest.raises(ToolError) as e:
        validate(index, "HUBSPOT_CREATE_DEAL", {"dealname": "x", "bogus": 1})
    assert "Unknown argument" in str(e.value)

    with pytest.raises(ToolError) as e:
        validate(index, "HUBSPOT_CREATE_DEAL", {})
    assert "missing required" in str(e.value)

    assert validate(index, "HUBSPOT_CREATE_DEAL", {"dealname": "x"}) == {"dealname": "x"}


def test_validate_coerces_primitive_types():
    from pa.services.tool_runner import validate

    index = {
        "T": {
            "app": "x", "required": [], "classification": "write",
            "properties": {"n": {"type": "integer"}, "b": {"type": "boolean"},
                           "a": {"type": "array"}},
        }
    }
    out = validate(index, "T", {"n": "5", "b": "true", "a": '["x"]'})
    assert out["n"] == 5 and out["b"] is True and out["a"] == ["x"]


def test_parse_tool_call_tolerates_fences_and_prose():
    from pa.services.tool_runner import parse_tool_call

    fenced = 'Sure!\n```json\n{"action":"tool","tool":"HUBSPOT_LIST_DEALS","args":{}}\n```'
    assert parse_tool_call(fenced) == {"tool": "HUBSPOT_LIST_DEALS", "args": {}}

    # Plain reply is not a tool call.
    assert parse_tool_call('{"action":"reply","text":"hi"}') is None
    assert parse_tool_call("just text") is None
    assert parse_tool_call("{broken json") is None


def test_write_tool_call_becomes_an_approval_proposal():
    from pa.graph.nodes import propose_action

    out = propose_action(
        {
            "request": "clean this up",
            "response": "ok",
            "tool_call": {"tool": "HUBSPOT_DELETE_CONTACT", "app": "hubspot",
                          "args": {"id": "1"}, "classification": "write"},
        }
    )
    assert out["approval_status"] == "pending"
    assert out["proposed_action"]["action"] == "composio_tool"
    assert out["proposed_action"]["tool"] == "HUBSPOT_DELETE_CONTACT"


def test_read_tool_call_is_not_gated():
    from pa.graph.nodes import propose_action

    out = propose_action(
        {
            "request": "show my deals",
            "response": "ok",
            "tool_call": {"tool": "HUBSPOT_LIST_DEALS", "app": "hubspot",
                          "args": {}, "classification": "read"},
        }
    )
    assert out.get("approval_status") == "not_required"
    assert "proposed_action" not in out


def test_every_router_destination_is_declared():
    """A router returning an undeclared destination raises KeyError at runtime."""
    from pa.graph import builder as b

    for fn, dests in ((b.after_plan, b.PLAN_DESTINATIONS),
                      (b.after_verify, b.VERIFY_DESTINATIONS)):
        for state in ({"error": "boom"},
                      {"tool_call": {"classification": "read"}},
                      {"tool_call": {"classification": "write"}},
                      {}):
            got = fn(state)
            assert got in dests, f"{fn.__name__}({state}) -> {got!r} not in {dests}"


def test_plan_router_sends_reads_to_execution():
    from pa.graph import builder as b

    assert b.after_plan({"tool_call": {"classification": "read"}}) == "run_read_tool"
    assert b.after_plan({"tool_call": {"classification": "write"}}) == "verify_result"
    assert b.after_plan({}) == "verify_result"


def test_graph_builds():
    from pa.graph.builder import build_graph

    assert build_graph() is not None


# --- Per-app dashboard insights ---


class _Run:
    """Records the tools an insight recipe asked for."""

    def __init__(self, payloads: dict):
        self.payloads = payloads
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, tool, args):
        self.calls.append((tool, args))
        return self.payloads.get(tool, "{}")


from datetime import UTC, datetime, timedelta as _td

_SOON = (datetime.now(UTC) + _td(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
_FAR = (datetime.now(UTC) + _td(days=900)).strftime("%Y-%m-%dT%H:%M:%SZ")

DEALS = json.dumps(
    {
        "results": [
            {"properties": {"dealname": "Big One", "dealstage": "appointmentscheduled",
                            "amount": "50000", "closedate": _FAR}},
            {"properties": {"dealname": "Small One", "dealstage": "qualifiedtobuy",
                            "amount": "1000", "closedate": None}},
            {"properties": {"dealname": "Won", "dealstage": "closedwon", "amount": "9000"}},
            {"properties": {"dealname": "Lost", "dealstage": "closedlost", "amount": "0"}},
            {"properties": {"dealname": "Closing Soon", "dealstage": "presentationscheduled",
                            "amount": "7500", "closedate": _SOON}},
        ]
    }
)


def test_hubspot_insights_computes_pipeline(monkeypatch):
    from pa.services import app_insights as ai

    run = _Run({"HUBSPOT_LIST_DEALS": DEALS})
    out = ai._hubspot("u1", run)
    assert run.calls == [("HUBSPOT_LIST_DEALS", {"limit": ai.LIST_LIMIT})]
    # closedwon / closedlost must not count as open.
    assert "3 open deals" in out["headline"]
    assert "$58,500" in out["headline"]
    labels = {s["label"]: s["value"] for s in out["stats"]}
    assert labels["Open deals"] == "3"
    assert labels["Closing in 30d"] == "1"
    assert labels["Total records"] == "5"


def test_hubspot_insights_handles_empty():
    from pa.services import app_insights as ai

    out = ai._hubspot("u1", _Run({"HUBSPOT_LIST_DEALS": "{}"}))
    assert out["headline"] == "No deals found"
    assert out["stats"] == []


def test_hubspot_insights_tolerates_bad_amounts():
    from pa.services import app_insights as ai

    payload = json.dumps({"results": [{"properties": {
        "dealname": "Weird", "dealstage": "appointmentscheduled", "amount": "not-a-number",
        "closedate": None}}]})
    out = ai._hubspot("u1", _Run({"HUBSPOT_LIST_DEALS": payload}))
    assert out["stats"][1]["value"] == "$0"


def test_insights_refuse_to_run_write_tools(monkeypatch):
    """A recipe must never be able to invoke a write tool."""
    from pa.services import app_insights as ai
    from pa.services import tool_runner as tr

    def boom(*_a, **_k):
        raise AssertionError("insights must not execute")

    monkeypatch.setattr(tr, "tool_index", lambda _u: {
        "HUBSPOT_LIST_DEALS": {"app": "hubspot", "classification": "read",
                               "required": [],
                               "properties": {"limit": {"type": "integer"}}},
        "HUBSPOT_CREATE_DEAL": {"app": "hubspot", "classification": "write",
                                "required": [], "properties": {}},
    })
    captured = {}

    def fake_execute(_u, tool, args):
        captured["tool"] = tool
        return {"tool": tool, "result": DEALS}

    monkeypatch.setattr(tr, "execute", fake_execute)
    out = ai.app_insights("u1", "hubspot")
    assert captured["tool"] == "HUBSPOT_LIST_DEALS"
    assert out["available"] is True


def test_insights_fall_back_for_apps_without_a_recipe(monkeypatch):
    from pa.services import app_insights as ai
    from pa.services import tool_runner as tr

    monkeypatch.setattr(tr, "tool_index", lambda _u: {
        "NOTION_LIST_PAGES": {"app": "notion", "classification": "read",
                              "required": [], "properties": {}},
    })
    out = ai.app_insights("u1", "notion")
    assert out["headline"] == "notion is connected"
    assert out["stats"][0] == {"label": "Tools available", "value": "1"}
