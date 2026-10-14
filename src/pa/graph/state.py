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
    proposed_action: dict
    approval_status: str  # not_required | pending | approved | rejected
    error: str
