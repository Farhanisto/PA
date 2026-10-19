"""StateGraph definition with explicit routing and termination paths."""

from typing import Any

from langgraph.graph import END, START, StateGraph

from pa.graph.nodes import (
    approval_gate,
    enrich_artifacts,
    finalize,
    generate_response,
    plan_action,
    prepare_context,
    propose_action,
    run_read_tool,
    summarize_tool_result,
    understand_request,
    verify_result,
)
from pa.graph.state import AssistantState


PLAN_DESTINATIONS = ("run_read_tool", "generate_response", "finalize")
VERIFY_DESTINATIONS = ("propose_action", "finalize")


def after_plan(state: dict) -> str:
    """Reads run immediately; anything else is answered in prose.

    The prose answer is only written when no tool ran: a tool's summary
    replaces it anyway, so writing it first would spend a model call on
    prose nobody reads.
    """
    call = state.get("tool_call") or {}
    if state.get("error"):
        return "finalize"
    if call and call.get("classification") == "read":
        return "run_read_tool"
    return "generate_response"


def after_verify(state: dict) -> str:
    if state.get("error"):
        return "finalize"
    return "propose_action"


def build_graph(checkpointer: Any = None):
    builder = StateGraph(AssistantState)
    builder.add_node("understand_request", understand_request)
    builder.add_node("prepare_context", prepare_context)
    builder.add_node("generate_response", generate_response)
    builder.add_node("plan_action", plan_action)
    builder.add_node("run_read_tool", run_read_tool)
    builder.add_node("enrich_artifacts", enrich_artifacts)
    builder.add_node("summarize_tool_result", summarize_tool_result)
    builder.add_node("verify_result", verify_result)
    builder.add_node("propose_action", propose_action)
    builder.add_node("approval_gate", approval_gate)
    builder.add_node("finalize", finalize)

    builder.add_edge(START, "understand_request")
    builder.add_edge("understand_request", "prepare_context")
    builder.add_edge("prepare_context", "plan_action")

    builder.add_conditional_edges("plan_action", after_plan, list(PLAN_DESTINATIONS))
    builder.add_edge("run_read_tool", "enrich_artifacts")
    builder.add_edge("enrich_artifacts", "summarize_tool_result")
    builder.add_edge("summarize_tool_result", "finalize")
    builder.add_edge("generate_response", "verify_result")

    builder.add_conditional_edges("verify_result", after_verify, list(VERIFY_DESTINATIONS))
    builder.add_edge("propose_action", "approval_gate")
    builder.add_edge("approval_gate", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)


def get_thread_config(thread_id: str, **kwargs: Any) -> dict:
    return {"configurable": {"thread_id": thread_id, **kwargs}}
