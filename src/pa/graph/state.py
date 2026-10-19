"""Typed graph state — only info needed to execute a workflow."""

from typing import TypedDict


class AssistantState(TypedDict, total=False):
    user_id: str
    request: str
    workflow: str  # briefing | action | general
    response: str
    task_items: list[str]
    notes: list[str]
    context: str
    app_context: str
    connected_apps: list[str]
    target_app: str  # the connected app this request is about
    unconnected_app: str  # an app the user named that is not connected
    focus: dict  # the record the user is acting on
    history: list[dict]  # earlier turns of this conversation
    tool_call: dict
    tool_result: dict
    artifacts: list[dict]  # structured app records surfaced as chat cards
    proposed_action: dict
    approval_status: str  # not_required | pending | approved | rejected
    error: str
