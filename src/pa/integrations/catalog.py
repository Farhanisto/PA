"""Composio app catalog: which apps users can connect.

Composio owns the integration surface, so the catalog is fetched from
Composio's toolkit list rather than hardcoded. There is no hardcoded fallback
list: if Composio is unconfigured or unreachable, the catalog is simply empty
rather than showing apps that cannot actually be connected.
"""

from __future__ import annotations

import logging
from typing import Any

from pa.integrations import composio_client

logger = logging.getLogger(__name__)

# Apps we surface first, in this order, when present in the catalog.
PREFERRED_ORDER = ("gmail", "outlook", "googlecalendar", "slack", "notion", "github")


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


def list_apps(limit: int = 60) -> list[dict[str, str]]:
    """Return the Composio catalog, or an empty list if it cannot be reached."""
    if not composio_client.is_configured():
        return []
    try:
        client = composio_client.get_client()
        resp = client.client.toolkits.list(type="all", limit=limit)
        items = getattr(resp, "items", None) or getattr(resp, "data", None) or []
        if isinstance(items, dict):
            items = items.get("items", []) or items.get("data", [])
        apps = [a for a in (_coerce(i) for i in items or []) if a]
    except Exception as exc:
        logger.warning("composio catalog unavailable: %s", type(exc).__name__)
        return []

    if not apps:
        return []

    rank = {slug: i for i, slug in enumerate(PREFERRED_ORDER)}
    apps.sort(key=lambda a: (rank.get(a["slug"], 99), a["name"].lower()))
    return apps


def connection_states(user_id: str, slugs: list[str]) -> dict[str, str]:
    """Map slug -> connection state for one user (best effort)."""
    states: dict[str, str] = {}
    for slug in slugs:
        try:
            states[slug] = composio_client.connection_state(user_id, slug)
        except Exception:
            states[slug] = "unavailable"
    return states


def catalog_status() -> dict[str, str]:
    """Describe why the catalog is empty, so the UI can say something useful.

    Distinguishes "server has no key", "key was rejected", and "key works".
    """
    if not composio_client.is_configured():
        return {"configured": "false", "reachable": "false", "app_count": "0",
                "reason": "COMPOSIO_API_KEY is not set on the server."}
    try:
        client = composio_client.get_client()
        resp = client.client.toolkits.list(type="all", limit=1)
        items = getattr(resp, "items", None) or getattr(resp, "data", None) or []
        if isinstance(items, dict):
            items = items.get("items", []) or items.get("data", [])
        return {"configured": "true", "reachable": "true", "app_count": str(len(items or [])),
                "reason": ""}
    except Exception as exc:
        detail = str(exc)
        if "uthentication" in detail or "APIKey" in detail or "401" in detail:
            reason = "The Composio API key was rejected. Check COMPOSIO_API_KEY."
        else:
            reason = f"Composio is unreachable: {type(exc).__name__}."
        return {"configured": "true", "reachable": "false", "app_count": "0", "reason": reason}
