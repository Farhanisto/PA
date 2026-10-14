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
    assert not is_action_allowed(tool_name="generate_briefing", user_id="", args={})
    assert is_action_allowed(tool_name="generate_briefing", user_id="u", args={"tasks": ["a"]})


def test_invalid_tool_args_rejected():
    from pa.tools.registry import invoke_tool

    with pytest.raises(ValueError, match="not allowed"):
        invoke_tool("nonexistent_tool", {})
    with pytest.raises(ValueError):
        invoke_tool("generate_briefing", {"tasks": "not-a-list"})


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


def test_briefing_tool_builds():
    from pa.tools.briefing import build_briefing

    out = build_briefing(tasks=["Ship MVP"], notes=["note"], preferences=["concise"])
    assert "Ship MVP" in out and "concise" in out
