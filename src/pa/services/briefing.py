"""Where a briefing gets its records, per connected app.

A briefing should cover everything the user has connected, not just their
mailbox. Each app contributes a couple of *read* tools whose records become
cards, so the briefing opens with what needs attention across the whole
workspace.

The tools are discovered rather than hardcoded: a slug that was renamed
between SDK versions would otherwise leave an app silently empty. Each
source lists exact slugs first, then patterns to fall back on.

Adding an app is one entry here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# How many records to pull per source, and how many cards to keep. The
# briefing is a summary, not a data dump — pagination covers the rest.
RECORDS_PER_SOURCE = 10
MAX_CARDS_PER_SOURCE = 10
MAX_CARDS_TOTAL = 60


@dataclass(frozen=True)
class Source:
    """One read tool whose records become briefing cards."""

    group: str
    tools: tuple[tuple[str, dict[str, Any]], ...] = ()
    patterns: tuple[str, ...] = ()
    args: dict[str, Any] = field(default_factory=dict)


# Per-app sources. The keys are toolkit slugs.
SOURCES: dict[str, list[Source]] = {
    "hubspot": [
        Source(
            group="Tickets",
            tools=(("HUBSPOT_LIST_TICKETS", {"limit": RECORDS_PER_SOURCE}),),
            patterns=("*LIST_TICKET*",),
        ),
        Source(
            group="Deals",
            tools=(("HUBSPOT_LIST_DEALS", {"limit": RECORDS_PER_SOURCE}),),
            patterns=("*LIST_DEAL*",),
        ),
        Source(
            group="Contacts",
            tools=(("HUBSPOT_LIST_CONTACTS", {"limit": RECORDS_PER_SOURCE}),),
            patterns=("*LIST_CONTACT*",),
        ),
    ],
}


def _pattern_score(slug: str) -> int:
    """Prefer the "list them all" tool over a by-id or single-record one."""
    score = 0
    if slug.endswith("S"):
        score += 3
    if "BY_ID" in slug or "_BY_" in slug:
        score -= 5
    if any(v in slug for v in ("LIST", "SEARCH", "GET_ALL", "RETRIEVE_ALL", "GET")):
        score += 1
    return score


def _resolve_tool(index: dict, source: Source) -> tuple[str, dict] | None:
    """The tool this source should call, and its arguments."""
    for slug, args in source.tools:
        if slug in index and index[slug].get("classification") == "read":
            merged = {**source.args, **args}
            return slug, merged

    if not source.patterns:
        return None
    from fnmatch import fnmatch

    found: dict[str, int] = {}
    for slug, meta in index.items():
        if meta.get("classification") != "read":
            continue
        for pattern in source.patterns:
            if fnmatch(slug, pattern):
                found[slug] = max(found.get(slug, -99), _pattern_score(slug))
    if not found:
        return None
    best = sorted(found.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    return best, dict(source.args)


def build_app_cards(
    user_id: str,
    app: str,
    index: dict | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Cards and per-source summaries for one app.

    Returns ``(cards, sources)`` where each source summary is
    ``{"app", "group", "count", "available"}`` — so a briefing can say
    "HubSpot · Tickets: 3" even when there is nothing to show.
    """
    from pa.services.artifacts import build_cards

    if index is None:
        from pa.services.tool_runner import tool_index

        try:
            index = tool_index(user_id)
        except Exception:
            index = {}

    sources = SOURCES.get((app or "").lower(), [])
    if not sources or not index:
        return [], []

    cards: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for source in sources:
        resolved = _resolve_tool(index, source)
        if resolved is None:
            summaries.append(
                {"app": app, "group": source.group, "count": 0, "available": False}
            )
            continue
        slug, args = resolved
        try:
            from pa.services.tool_runner import execute_read

            payload = execute_read(user_id, slug, args)
        except Exception as exc:
            logger.info(
                "briefing source %s/%s failed: %s", app, source.group, type(exc).__name__
            )
            summaries.append(
                {"app": app, "group": source.group, "count": 0, "available": False}
            )
            continue

        built = build_cards(slug, app, payload)[:MAX_CARDS_PER_SOURCE]
        for card in built:
            card["group"] = f"{source.group}"
        cards.extend(built)
        summaries.append(
            {"app": app, "group": source.group, "count": len(built), "available": True}
        )

    return cards[:MAX_CARDS_TOTAL], summaries
