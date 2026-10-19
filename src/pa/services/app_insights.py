"""Per-app dashboard insights.

Each connected app gets a small "summary recipe": a couple of read-only tools
that produce the numbers worth putting on a dashboard. Apps without a recipe
still get a useful (if plainer) summary rather than nothing.

All of this runs through read-classified tools only, so nothing here can
mutate a user's data.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

logger = logging.getLogger(__name__)

# How many records to pull per feed; enough for a summary, cheap to fetch.
LIST_LIMIT = 100


def _iso(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _results(payload: Any) -> list[dict]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return []
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("results", "data", "items", "records"):
            inner = payload.get(key)
            if isinstance(inner, list):
                return [x for x in inner if isinstance(x, dict)]
    return []


CLOSED_STAGES = {"closedwon", "closedlost", "closed_won", "closed_lost"}


def _hubspot(user_id: str, run: Callable[[str, dict], str]) -> dict[str, Any]:
    """Pipeline health: open deals, value, and what's closing soon."""
    raw = run("HUBSPOT_LIST_DEALS", {"limit": LIST_LIMIT})
    deals = _results(raw)
    if not deals:
        return {
            "headline": "No deals found",
            "stats": [],
            "items": [],
        }

    now = datetime.now(UTC)
    soon_cutoff = now + timedelta(days=30)
    open_deals: list[dict] = []
    pipeline_value = 0.0
    closing_soon: list[dict] = []

    for deal in deals:
        props = deal.get("properties") or {}
        stage = str(props.get("dealstage") or "").lower()
        name = props.get("dealname") or "(unnamed deal)"
        try:
            amount = float(props.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        if stage not in CLOSED_STAGES:
            open_deals.append({"name": name, "stage": stage, "amount": amount})
            pipeline_value += amount
            close = _iso(props.get("closedate"))
            if close and now <= close <= soon_cutoff:
                closing_soon.append(
                    {"name": name, "amount": amount, "closes": close.strftime("%d %b %Y")}
                )

    open_deals.sort(key=lambda d: d["amount"], reverse=True)
    closing_soon.sort(key=lambda d: d["closes"])

    stats = [
        {"label": "Open deals", "value": str(len(open_deals))},
        {"label": "Pipeline value", "value": f"${pipeline_value:,.0f}"},
        {"label": "Closing in 30d", "value": str(len(closing_soon))},
        {"label": "Total records", "value": str(len(deals))},
    ]
    items = [
        {"title": d["name"], "detail": d["stage"] or "no stage",
         "meta": f"${d['amount']:,.0f}" if d["amount"] else ""}
        for d in open_deals[:6]
    ]
    headline = (
        f"{len(open_deals)} open deal{'s' if len(open_deals) != 1 else ''} "
        f"worth ${pipeline_value:,.0f}"
    )
    return {"headline": headline, "stats": stats, "items": items}


def _gmail(user_id: str, run: Callable[[str, dict], str]) -> dict[str, Any]:
    """Mailbox shape, from whatever PA has already triaged."""
    msgs = _results(run("GMAIL_FETCH_EMAILS", {"maxResults": LIST_LIMIT, "q": "in:inbox"}))
    if not msgs:
        return {"headline": "Inbox is empty", "stats": [], "items": []}
    stats = [
        {"label": "In inbox", "value": str(len(msgs))},
    ]
    return {
        "headline": f"{len(msgs)} message{'s' if len(msgs) != 1 else ''} in the inbox",
        "stats": stats,
        "items": [
            {"title": m.get("subject") or "(no subject)", "detail": m.get("from", ""), "meta": ""}
            for m in msgs[:6]
        ],
    }


# Apps with a bespoke dashboard summary.
RECIPES: dict[str, Callable[[str, Callable[[str, dict], str]], dict[str, Any]]] = {
    "hubspot": _hubspot,
    "gmail": _gmail,
}


def app_insights(user_id: str, app: str) -> dict[str, Any]:
    """Summarise one connected app. Never raises."""
    from pa.services.tool_runner import ToolError, execute, tool_index, validate

    base = {"app": app, "headline": "", "stats": [], "items": [], "available": False}

    index = tool_index(user_id)
    if not index:
        return {**base, "headline": "No connected apps"}

    recipe = RECIPES.get(app)
    if recipe is None:
        count = sum(1 for m in index.values() if m["app"] == app)
        return {
            **base,
            "headline": f"{app} is connected",
            "stats": [{"label": "Tools available", "value": str(count)}],
            "items": [],
        }

    # Only ever run read-classified tools for a dashboard.
    def run(tool: str, args: dict) -> str:
        meta = index.get(tool)
        if not meta or meta["classification"] != "read":
            raise ToolError(f"{tool} is not a permitted read tool")
        return execute(user_id, tool, validate(index, tool, args))["result"]

    try:
        summary = recipe(user_id, run)
    except ToolError as exc:
        return {**base, "headline": f"Could not summarise {app}: {exc}"[:160]}
    except Exception as exc:
        logger.warning("insights failed for %s: %s", app, type(exc).__name__)
        return {**base, "headline": f"Could not summarise {app}: {type(exc).__name__}"[:160]}

    return {**base, **summary, "available": True}