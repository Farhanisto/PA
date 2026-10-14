"""Allowlisted tool registry — model output can never invoke arbitrary tools."""

from collections.abc import Callable
from typing import Any

from pa.tools.briefing import build_briefing

ALLOWED_TOOLS: dict[str, Callable[..., str]] = {}


def register(name: str):
    def deco(fn: Callable[..., str]):
        ALLOWED_TOOLS[name] = fn
        return fn

    return deco


@register("generate_briefing")
def generate_briefing(tasks: list[str] | None = None, notes: list[str] | None = None) -> str:
    if tasks is not None and not isinstance(tasks, list):
        raise ValueError("tasks must be a list of strings")
    if notes is not None and not isinstance(notes, list):
        raise ValueError("notes must be a list of strings")
    for item in (tasks or []) + (notes or []):
        if not isinstance(item, str):
            raise ValueError("briefing items must be strings")
        if len(item) > 2000:
            raise ValueError("briefing item too long")
    return build_briefing(tasks=tasks, notes=notes)


@register("approved_echo")
def approved_echo(message: str) -> str:
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message must be a non-empty string")
    if len(message) > 2000:
        raise ValueError("message too long")
    return f"Executed approved action: {message[:500]}"


def invoke_tool(name: str, args: dict[str, Any] | None = None) -> str:
    """Validate tool name + args before any side effect."""
    if name not in ALLOWED_TOOLS:
        raise ValueError(f"tool not allowed: {name}")
    fn = ALLOWED_TOOLS[name]
    return fn(**(args or {}))


def is_allowed(name: str) -> bool:
    return name in ALLOWED_TOOLS
