"""Run/tool-call tracing with latency, token, and cost accounting.

Requirement §7: trace LangGraph runs, tool calls, failures, latency, costs.

Cost is estimated from provider pricing tables (USD per 1K tokens). Tables are
best-effort defaults and overridable via env so pricing can be corrected
without a code change. Never store message bodies or secrets here — only
lengths, names, and sanitized summaries.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from pa.db.models import RunTrace, ToolCall
from pa.observability import redact_obj

# USD per 1K tokens: (prompt, completion).
PRICING: dict[str, tuple[float, float]] = {
    "groq": (0.00005, 0.00008),
    "nebius": (0.0002, 0.0006),
    "default": (0.0005, 0.0015),
}


def _pricing_for(model_provider: str) -> tuple[float, float]:
    import os

    override_p = os.getenv("PA_COST_PER_1K_PROMPT")
    override_c = os.getenv("PA_COST_PER_1K_COMPLETION")
    if override_p and override_c:
        try:
            return float(override_p), float(override_c)
        except ValueError:
            pass
    return PRICING.get(model_provider, PRICING["default"])


def estimate_cost(provider: str, prompt_tokens: int, completion_tokens: int) -> float:
    p_rate, c_rate = _pricing_for(provider)
    return round((prompt_tokens / 1000.0) * p_rate + (completion_tokens / 1000.0) * c_rate, 8)


def current_provider() -> str:
    try:
        from pa.models.factory import resolve_provider

        return resolve_provider()
    except Exception:
        return "default"


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) for tracing without a tokenizer."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _summarize(value: Any, limit: int = 300) -> str:
    """Length-preserving summary: never store full email bodies here."""
    if value is None:
        return ""
    if isinstance(value, str):
        return f"<{len(value)} chars> {value[:limit]}"
    try:
        text = json.dumps(redact_obj(value), default=str)
    except Exception:
        text = str(value)
    return text[:limit]


def record_tool_call(
    db: Session,
    *,
    user_id: str,
    job_id: str,
    tool_name: str,
    args: dict[str, Any] | None = None,
    status: str = "ok",
    latency_ms: int = 0,
    output: Any = None,
    error: str = "",
) -> ToolCall:
    call = ToolCall(
        user_id=user_id,
        job_id=job_id,
        tool_name=tool_name[:64],
        args=json.dumps(redact_obj(args or {}), default=str),
        status=status[:16],
        latency_ms=max(0, int(latency_ms)),
        output_summary=_summarize(output),
        error=str(error or "")[:1000],
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


@contextmanager
def trace_tool(
    db: Session, *, user_id: str, job_id: str, tool_name: str, args: dict[str, Any] | None = None
) -> Iterator[dict[str, Any]]:
    """Time a tool call and persist it. Usage:
    with trace_tool(...) as rec:
        result = do_work()
        rec["output"] = result
    """
    rec: dict[str, Any] = {"output": None}
    start = time.monotonic()
    status, error = "ok", ""
    try:
        yield rec
    except Exception as exc:
        status, error = "error", f"{type(exc).__name__}: {exc}"
        raise
    finally:
        latency = int((time.monotonic() - start) * 1000)
        try:
            record_tool_call(
                db,
                user_id=user_id,
                job_id=job_id,
                tool_name=tool_name,
                args=args,
                status=status,
                latency_ms=latency,
                output=rec.get("output"),
                error=error,
            )
        except Exception:
            # Tracing must never break the caller.
            pass


def record_run(
    db: Session,
    *,
    user_id: str,
    job_id: str,
    node: str,
    thread_id: str = "",
    status: str = "ok",
    latency_ms: int = 0,
    prompt_text: str = "",
    completion_text: str = "",
    error: str = "",
) -> RunTrace:
    provider = current_provider()
    p_tokens = estimate_tokens(prompt_text)
    c_tokens = estimate_tokens(completion_text)
    trace = RunTrace(
        user_id=user_id,
        job_id=job_id,
        thread_id=(thread_id or "")[:36],
        node=node[:64],
        status=status[:16],
        latency_ms=max(0, int(latency_ms)),
        prompt_tokens=p_tokens,
        completion_tokens=c_tokens,
        cost_usd=estimate_cost(provider, p_tokens, c_tokens),
        error=str(error or "")[:1000],
    )
    db.add(trace)
    db.commit()
    db.refresh(trace)
    return trace


def list_traces(db: Session, *, user_id: str, job_id: str, limit: int = 200) -> list[RunTrace]:
    stmt = (
        select(RunTrace)
        .where(RunTrace.user_id == user_id, RunTrace.job_id == job_id)
        .order_by(RunTrace.created_at.asc())
        .limit(max(1, min(limit, 1000)))
    )
    return list(db.scalars(stmt).all())


def list_tool_calls(db: Session, *, user_id: str, job_id: str, limit: int = 200) -> list[ToolCall]:
    stmt = (
        select(ToolCall)
        .where(ToolCall.user_id == user_id, ToolCall.job_id == job_id)
        .order_by(ToolCall.created_at.asc())
        .limit(max(1, min(limit, 1000)))
    )
    return list(db.scalars(stmt).all())


def job_summary(db: Session, *, user_id: str, job_id: str) -> dict[str, Any]:
    traces = list_traces(db, user_id=user_id, job_id=job_id)
    calls = list_tool_calls(db, user_id=user_id, job_id=job_id)
    return {
        "traces": traces,
        "tool_calls": calls,
        "total_latency_ms": sum(t.latency_ms for t in traces) + sum(c.latency_ms for c in calls),
        "total_cost_usd": round(sum(t.cost_usd for t in traces), 8),
        "failures": sum(1 for t in traces if t.status != "ok")
        + sum(1 for c in calls if c.status != "ok"),
        "total_tokens": sum(t.prompt_tokens + t.completion_tokens for t in traces),
    }


def record_model_call(
    db: Session,
    *,
    user_id: str,
    job_id: str,
    node: str,
    prompt: str,
    completion: str,
    latency_ms: int,
    thread_id: str = "",
) -> Optional[RunTrace]:
    return record_run(
        db,
        user_id=user_id,
        job_id=job_id,
        node=node,
        thread_id=thread_id,
        status="ok",
        latency_ms=latency_ms,
        prompt_text=prompt,
        completion_text=completion,
    )
