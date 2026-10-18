"""Observability routes: per-run traces, tool calls, latency, and cost (req §7)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from pa.api.dependencies import get_current_user, get_db
from pa.db.models import ToolCall
from pa.schemas.auth import ObservabilitySummary, RunTraceResponse, ToolCallResponse

router = APIRouter(prefix="/observability", tags=["observability"])


def _tool_response(c: ToolCall) -> ToolCallResponse:
    try:
        args = json.loads(c.args or "{}")
    except json.JSONDecodeError:
        args = {}
    if not isinstance(args, dict):
        args = {"value": args}
    return ToolCallResponse(
        id=c.id,
        job_id=c.job_id,
        tool_name=c.tool_name,
        args=args,
        status=c.status,
        latency_ms=c.latency_ms,
        output_summary=c.output_summary,
        error=c.error,
        created_at=c.created_at,
    )


@router.get(
    "/jobs/{job_id}",
    response_model=ObservabilitySummary,
    summary="Trace a run",
    description="Per-node traces and tool calls with latency, token counts, cost, and failures for a job.",
)
def job_observability(
    job_id: str, db: Session = Depends(get_db), user_id: str = Depends(get_current_user)
):
    """Return the full trace for one of the caller's jobs."""
    from pa.services import tracing

    summary = tracing.job_summary(db, user_id=user_id, job_id=job_id)
    return ObservabilitySummary(
        job_id=job_id,
        traces=[
            RunTraceResponse(
                id=t.id,
                job_id=t.job_id,
                thread_id=t.thread_id,
                node=t.node,
                status=t.status,
                latency_ms=t.latency_ms,
                prompt_tokens=t.prompt_tokens,
                completion_tokens=t.completion_tokens,
                cost_usd=t.cost_usd,
                error=t.error,
                created_at=t.created_at,
            )
            for t in summary["traces"]
        ],
        tool_calls=[_tool_response(c) for c in summary["tool_calls"]],
        total_latency_ms=summary["total_latency_ms"],
        total_cost_usd=summary["total_cost_usd"],
        failures=summary["failures"],
    )


@router.get(
    "/tool-calls",
    response_model=list[ToolCallResponse],
    summary="Recent tool calls",
)
def recent_tool_calls(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """Tool-call history for the caller, newest first."""
    from sqlalchemy import select

    calls = db.scalars(
        select(ToolCall)
        .where(ToolCall.user_id == user_id)
        .order_by(ToolCall.created_at.desc())
        .limit(limit)
    ).all()
    return [_tool_response(c) for c in calls]


@router.get("/costs", summary="Aggregate usage")
def cost_summary(db: Session = Depends(get_db), user_id: str = Depends(get_current_user)):
    """Aggregate token counts and estimated cost across the caller's runs."""
    from sqlalchemy import func, select

    from pa.db.models import RunTrace

    row = db.execute(
        select(
            func.coalesce(func.sum(RunTrace.prompt_tokens), 0),
            func.coalesce(func.sum(RunTrace.completion_tokens), 0),
            func.coalesce(func.sum(RunTrace.cost_usd), 0.0),
            func.count(RunTrace.id),
        ).where(RunTrace.user_id == user_id)
    ).one()
    return {
        "user_id": user_id,
        "runs": int(row[3]),
        "prompt_tokens": int(row[0]),
        "completion_tokens": int(row[1]),
        "total_tokens": int(row[0]) + int(row[1]),
        "total_cost_usd": round(float(row[2]), 8),
    }
