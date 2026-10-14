"""StateGraph definition with explicit routing and termination paths."""

from typing import Any

from langgraph.graph import END, START, StateGraph

from pa.graph.nodes import (
    approval_gate,
    finalize,
    generate_response,
    prepare_context,
    propose_action,
    understand_request,
    verify_result,
)
from pa.graph.state import AssistantState


def build_graph(checkpointer: Any = None):
    builder = StateGraph(AssistantState)
    builder.add_node("understand_request", understand_request)
    builder.add_node("prepare_context", prepare_context)
    builder.add_node("generate_response", generate_response)
    builder.add_node("verify_result", verify_result)
    builder.add_node("propose_action", propose_action)
    builder.add_node("approval_gate", approval_gate)
    builder.add_node("finalize", finalize)

    builder.add_edge(START, "understand_request")
    builder.add_edge("understand_request", "prepare_context")
    builder.add_edge("prepare_context", "generate_response")
    builder.add_edge("generate_response", "verify_result")

    def after_verify(state: dict) -> str:
        if state.get("error"):
            return "finalize"
        return "propose_action"

    builder.add_conditional_edges("verify_result", after_verify, ["propose_action", "finalize"])
    builder.add_edge("propose_action", "approval_gate")
    builder.add_edge("approval_gate", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)


def get_thread_config(thread_id: str, **kwargs: Any) -> dict:
    return {"configurable": {"thread_id": thread_id, **kwargs}}
