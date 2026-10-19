"""Composio app catalog: which apps users can connect.

Composio owns the integration surface, so the catalog is fetched from
Composio's toolkit list rather than hardcoded. There is no hardcoded fallback
list: if Composio is unconfigured or unreachable, the catalog is simply empty
rather than showing apps that cannot actually be connected.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from pa.integrations import composio_client

logger = logging.getLogger(__name__)

# Apps we surface first, in this order, when present in the catalog.
PREFERRED_ORDER = ("gmail", "hubspot", "outlook", "googlecalendar", "slack", "notion", "github")


def _coerce(item: Any) -> dict[str, str] | None:
    slug = ""
    name = ""
    desc = ""
    if isinstance(item, dict):
        slug = str(item.get("slug") or item.get("name") or "")
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        name = str(item.get("display_name") or meta.get("display_name") or item.get("name") or "")
        desc = str(meta.get("description") or item.get("description") or "")
        logo = str(item.get("logo") or "")
    else:
        slug = str(getattr(item, "slug", "") or "")
        meta = getattr(item, "meta", None)
        if isinstance(meta, dict):
            name = str(meta.get("display_name") or "")
            desc = str(meta.get("description") or "")
        name = name or str(getattr(item, "display_name", "") or "")
        desc = desc or str(getattr(item, "description", "") or "")
        logo = str(getattr(item, "logo", "") or "")
    if not slug:
        return None
    return {
        "slug": slug.lower(),
        "name": name or slug.replace("-", " ").title(),
        "description": (desc or "Connect this app.")[:160],
        "logo": logo,
    }


_CATALOG_TTL_SECONDS = 300.0
_catalog_cache: dict[str, object] = {"at": 0.0, "apps": None}

_TOOL_TTL_SECONDS = 600.0
_tool_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _fetch_all_toolkits(page_size: int = 200, max_items: int = 2000) -> list[dict[str, str]]:
    """Walk Composio's cursor pagination and return the whole catalog.

    Composio exposes well over a thousand toolkits; requesting a single page
    silently truncated the catalog to whatever ``limit`` we happened to pass.
    """
    client = composio_client.get_client()
    collected: list[dict[str, str]] = []
    seen: set[str] = set()
    cursor = None
    for _ in range(50):  # hard stop against a runaway cursor loop
        kwargs: dict[str, Any] = {"type": "all", "limit": page_size}
        if cursor:
            kwargs["cursor"] = cursor
        resp = client.client.toolkits.list(**kwargs)
        items = getattr(resp, "items", None) or getattr(resp, "data", None) or []
        if isinstance(items, dict):
            items = items.get("items", []) or items.get("data", [])
        if not items:
            break
        for coerced in (_coerce(i) for i in items):
            if coerced and coerced["slug"] not in seen:
                seen.add(coerced["slug"])
                collected.append(coerced)
        if len(collected) >= max_items:
            break
        cursor = getattr(resp, "next_cursor", None)
        if not cursor:
            break
    return collected


def list_apps(limit: int | None = None, *, use_cache: bool = True) -> list[dict[str, str]]:
    """Return the full Composio catalog, or an empty list if unreachable.

    The toolkit list changes rarely, so it is cached; without this every page
    load paid several Composio round trips.
    """
    if not composio_client.is_configured():
        return []
    if use_cache:
        cached = _catalog_cache.get("apps")
        age = time.monotonic() - float(_catalog_cache.get("at") or 0.0)
        if cached is not None and age < _CATALOG_TTL_SECONDS:
            return list(cached)[:limit] if limit else list(cached)
    try:
        apps = _fetch_all_toolkits()
    except Exception as exc:
        logger.warning("composio catalog unavailable: %s", type(exc).__name__)
        return []

    if not apps:
        return []

    rank = {slug: i for i, slug in enumerate(PREFERRED_ORDER)}
    apps.sort(key=lambda a: (rank.get(a["slug"], 99), a["name"].lower()))
    _catalog_cache["apps"] = list(apps)
    _catalog_cache["at"] = time.monotonic()
    # The name matcher is built from the catalog, so it must not outlive it.
    invalidate_mentions()
    return apps[:limit] if limit else apps


# --- Mentioning an app by name --------------------------------------------
#
# The assistant must only ever talk about apps the user has connected. A
# prompt rule ("do not mention other apps") is not a boundary: a model asked
# about "tickets" with no context will happily name a famous ticketing app
# it has never been connected to. So the check is done here, against the
# real catalog.

# App names that are ordinary English words would match ordinary prose
# ("a task for you", "send a mail"), so they are not treated as app
# mentions. Everything else is a brand name and is safe to look for.
_GENERIC_APP_NAMES = frozenset({
    "asana", "chat", "chatgpt", "classroom", "cloud", "code", "contacts",
    "drive", "docs", "dropbox", "forms", "groups", "keep", "mail", "maps",
    "meet", "music", "news", "notes", "photos", "sheets", "sites", "slides",
    "storage", "tasks", "teams", "translate", "weather", "youtube",
})

_MENTION_TTL_SECONDS = 300.0
_mention_cache: dict[str, object] = {"at": 0.0, "regex": None, "names": {}}


def invalidate_mentions() -> None:
    """Drop the cached app-name matcher (it is built from the catalog)."""
    _mention_cache.update({"at": 0.0, "regex": None, "names": {}})


def _mention_pattern():
    """Compiled matcher for app names in the catalog, plus name -> slug.

    Cached alongside the catalog: rebuilding a thousand-alternative regex on
    every response would be wasteful.
    """
    now = time.monotonic()
    cached = _mention_cache.get("regex")
    if cached is not None and (now - float(_mention_cache.get("at") or 0.0)) < _MENTION_TTL_SECONDS:
        return cached

    import re

    names: dict[str, str] = {}
    for app in list_apps():
        slug = app["slug"]
        label = (app.get("name") or "").strip()
        # The slug is the most reliable name ("zendesk"), but a slug like
        # "googlecalendar" is not how anyone writes it; keep the display
        # name too.
        for candidate in (label, slug.replace("-", " "), slug):
            key = candidate.strip().lower()
            if len(key) < 5 or key in _GENERIC_APP_NAMES:
                continue
            names.setdefault(key, slug)
    if not names:
        _mention_cache["at"] = now
        _mention_cache["regex"] = None
        _mention_cache["names"] = {}
        return None
    # Longest first so "google calendar" wins over "google".
    ordered = sorted(names, key=len, reverse=True)
    pattern = re.compile(
        r"\b(?:" + "|".join(re.escape(n) for n in ordered) + r")\b", re.IGNORECASE
    )
    _mention_cache["at"] = now
    _mention_cache["regex"] = pattern
    _mention_cache["names"] = names
    return pattern


def mentioned_apps(text: str) -> list[str]:
    """Slugs of the catalog apps named in ``text``.

    Only distinctive brand names count, so ordinary prose is not mistaken
    for an app mention.
    """
    if not text or not text.strip():
        return []
    pattern = _mention_pattern()
    if pattern is None:
        return []
    names = _mention_cache.get("names") or {}
    found: list[str] = []
    for match in pattern.finditer(text):
        slug = names.get(match.group(0).strip().lower())
        if slug and slug not in found:
            found.append(slug)
    return found


def mentioned_unconnected_apps(text: str, connected_slugs: list[str]) -> list[str]:
    """Catalog apps named in ``text`` that the user has not connected."""
    connected = {s.lower() for s in connected_slugs}
    return [slug for slug in mentioned_apps(text) if slug not in connected]


def connection_states(user_id: str, slugs: list[str]) -> dict[str, str]:
    """Map slug -> connection state for one user, using a single batched call."""
    known = composio_client.connection_states(user_id)
    if known is None:
        # Composio unreachable: report honestly rather than "not connected".
        return {slug: "unavailable" for slug in slugs}
    return {slug: known.get(slug, "none") for slug in slugs}


def catalog_status() -> dict[str, str]:
    """Describe why the catalog is empty, so the UI can say something useful.

    Distinguishes "server has no key", "key was rejected", and "key works".
    """
    if not composio_client.is_configured():
        return {"configured": "false", "reachable": "false", "app_count": "0",
                "reason": "COMPOSIO_API_KEY is not set on the server."}
    try:
        apps = list_apps(limit=1, use_cache=False)
        return {"configured": "true", "reachable": "true", "app_count": str(len(apps)),
                "reason": ""}
    except Exception as exc:
        detail = str(exc)
        if "uthentication" in detail or "APIKey" in detail or "401" in detail:
            reason = "The Composio API key was rejected. Check COMPOSIO_API_KEY."
        else:
            reason = f"Composio is unreachable: {type(exc).__name__}."
        return {"configured": "true", "reachable": "false", "app_count": "0", "reason": reason}


def list_tools(provider: str, *, limit: int = 200, use_cache: bool = True) -> list[dict[str, Any]]:
    """List the Composio tools available for one toolkit.

    This is what the agent can actually call once the app is connected, so it
    is cached per toolkit (tool lists are near-static).
    """
    slug = composio_client.toolkit_slug(provider)
    now = time.monotonic()
    cached = _tool_cache.get(slug)
    if use_cache and cached is not None and (now - cached[0]) < _TOOL_TTL_SECONDS:
        return list(cached[1])[:limit]
    client = composio_client.get_client()
    resp = client.client.tools.list(toolkit_slug=slug, limit=limit)
    items = getattr(resp, "items", None) or getattr(resp, "data", None) or []
    if isinstance(items, dict):
        items = items.get("items", []) or items.get("data", [])
    out: list[dict[str, Any]] = []
    for item in items or []:
        if isinstance(item, dict):
            out.append(
                {
                    "slug": str(item.get("slug") or ""),
                    "name": str(item.get("name") or item.get("slug") or ""),
                    "description": str(item.get("description") or "")[:400],
                }
            )
        else:
            out.append(
                {
                    "slug": str(getattr(item, "slug", "") or ""),
                    "name": str(getattr(item, "name", None) or getattr(item, "slug", "") or ""),
                    "description": str(getattr(item, "description", "") or "")[:400],
                }
            )
    _tool_cache[slug] = (now, list(out))
    return out[:limit]
