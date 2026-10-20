"""Allowlisted tool registry — model output can never invoke arbitrary tools.

Tools are registered by name and validated before execution. Composio-backed
app actions are the only side-effecting tools, and every one of them requires
explicit user approval (see pa.security.policy).
"""

from collections.abc import Callable
from typing import Any

ALLOWED_TOOLS: dict[str, Callable[..., str]] = {}


def register(name: str):
    """Register a tool under an allowlisted name."""

    def deco(fn: Callable[..., str]):
        ALLOWED_TOOLS[name] = fn
        return fn

    return deco


@register("compose_email")
def compose_email(to: str, subject: str = "", body: str = "") -> str:
    """Prepare an email for review. Does NOT send — sending needs approval."""
    to = (to or "").strip()
    body = (body or "").strip()
    if not to or "@" not in to:
        raise ValueError("valid recipient email required")
    if not body:
        raise ValueError("body must not be empty")
    if len(to) > 320 or len(subject) > 998 or len(body) > 100000:
        raise ValueError("field too long")
    return f"Prepared email to {to} (subject: {subject or '(none)'}) for review."


def invoke_tool(name: str, args: dict[str, Any] | None = None) -> str:
    """Validate tool name + args before any side effect."""
    if name not in ALLOWED_TOOLS:
        raise ValueError(f"tool not allowed: {name}")
    return ALLOWED_TOOLS[name](**(args or {}))


def is_allowed(name: str) -> bool:
    return name in ALLOWED_TOOLS
