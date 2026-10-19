"""Connected-app awareness for the assistant.

The assistant must know what the user has actually connected and which tools
those apps expose, otherwise it answers as a generic chatbot and cannot act on
anything. Everything here is per user and never crosses accounts.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# How many tools to name per app in the prompt. Enough for the model to know
# what is possible without blowing the context window on HubSpot's 290 tools.
_MAX_TOOLS_PER_APP = 40


def connected_apps_context(user_id: str) -> dict:
    """Return {'text': ..., 'apps': [...], 'tools': {slug: [...]}}.

    Never raises: a Composio outage must degrade the assistant to "no apps",
    not fail the job.
    """
    from pa.integrations import catalog, composio_client

    try:
        states = composio_client.connection_states(user_id)
    except Exception as exc:
        logger.warning("could not read connection states: %s", type(exc).__name__)
        states = None

    if states is None:
        # Never claim the user has no apps when we simply could not check.
        return {
            "text": (
                "App connection status is currently unavailable. Do not assume the "
                "user has no apps, and do not invent app data. Say you are "
                "checking and cannot confirm right now."
            ),
            "apps": [],
            "tools": {},
        }

    active = sorted(slug for slug, state in states.items() if state == "active")
    if not active:
        return {
            "text": (
                "The user has NOT connected any apps yet. If they ask for app work, "
                "tell them to open the Apps section and connect one first. Do not "
                "invent app data."
            ),
            "apps": [],
            "tools": {},
        }

    tools_by_app: dict[str, list[dict]] = {}
    for slug in active:
        try:
            tools_by_app[slug] = catalog.list_tools(slug, limit=_MAX_TOOLS_PER_APP)
        except Exception:
            tools_by_app[slug] = []

    lines = ["The user has these apps connected and ready to use:"]
    for slug in active:
        tools = tools_by_app.get(slug) or []
        names = ", ".join(t["slug"] for t in tools[:_MAX_TOOLS_PER_APP])
        suffix = "" if len(tools) < _MAX_TOOLS_PER_APP else ", ..."
        lines.append(f"- {slug} ({len(tools)} tools): {names}{suffix}")

    lines.append(
        "Answer only about work these connected apps can do. If the request is "
        "outside them, say which app it would need and what the user should "
        "connect — do not drift into unrelated general chat."
    )

    return {"text": "\n".join(lines), "apps": active, "tools": tools_by_app}