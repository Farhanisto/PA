"""Unit tests: config, model adapter, graph routing, policy, memory, retry."""

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
