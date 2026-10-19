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
# The full list is only ever sent in the tool-selection prompt, which is
# ranked and size-bounded.
_MAX_TOOLS_PER_APP = 8


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
        label = _display_name(slug)
        lines.append(f"- {label} ({slug}, {len(tools)} tools): {names}{suffix}")

    lines.append(
        "Answer only about work these connected apps can do. If the request is "
        "outside them, say which app it would need and what the user should "
        "connect — do not drift into unrelated general chat."
    )
    lines.append(
        "Never name or describe any other app, and never present data from one, "
        "as if you had used it. If the user needs an app that is not connected, "
        "say only that they must connect it."
    )

    return {"text": "\n".join(lines), "apps": active, "tools": tools_by_app}


# --- Target-app detection --------------------------------------------------
#
# A request like "tickets" names no app, so the assistant cannot tell a
# HubSpot ticket from a Zendesk one. The app is usually named either in the
# request itself ("create a HubSpot ticket") or in the turn before it (the
# assistant asked "contacts, companies, deals or tickets?" and the user
# answered "tickets"). Detection pins the request to one app so the wrong
# app's tools are never offered or accepted.

# Apps whose slug is a common English word are only matched on their display
# name, never on the bare slug, to avoid nonsense like "I have a notion of".
_AMBIGUOUS_SLUGS = {"notion", "asana", "todoist", "monday", "trello", "dropbox", "zapier"}

_MIN_NAME_LEN = 4
# Below this length a spacing-insensitive match is too noisy: "slack" would
# match "slack in the schedule", "zoom" would match "zoom in".
_MIN_FLAT_LEN = 6


def _candidate_names(slug: str, display_name: str) -> list[str]:
    """The strings that can refer to this app in a user's message."""
    names: list[str] = []
    clean_slug = (slug or "").strip()
    if clean_slug and clean_slug.lower() not in _AMBIGUOUS_SLUGS:
        names.append(clean_slug)
        # "google-calendar" is also written "google calendar".
        names.append(clean_slug.replace("-", " "))
    label = (display_name or "").strip()
    if label and label.lower() not in {n.lower() for n in names}:
        names.append(label)
    return [n for n in dict.fromkeys(names) if len(n) >= _MIN_NAME_LEN]


def _display_names(slugs: list[str]) -> dict[str, str]:
    """Slug -> display name, from the (cached) catalog."""
    from pa.integrations import catalog

    out: dict[str, str] = {}
    try:
        for app in catalog.list_apps():
            if app["slug"] in slugs:
                out[app["slug"]] = app.get("name") or ""
    except Exception:
        pass
    return out


def _display_name(slug: str) -> str:
    """A friendly name for one app slug, e.g. "HubSpot"."""
    from pa.integrations import catalog

    try:
        for app in catalog.list_apps():
            if app["slug"] == slug:
                return app.get("name") or slug
    except Exception:
        pass
    return (slug or "").replace("-", " ").title()


def _mentions(text: str, candidates: list[str]) -> bool:
    """Does the text name this app?

    Two passes: an exact word match, and a spacing-insensitive one so that
    "google calendar" matches the slug "googlecalendar".
    """
    import re

    low = (text or "").lower()
    if not low.strip():
        return False
    for cand in candidates:
        if re.search(rf"\b{re.escape(cand.lower())}\b", low):
            return True
    flat_text = re.sub(r"[^a-z0-9]+", "", low)
    for cand in candidates:
        flat_cand = re.sub(r"[^a-z0-9]+", "", cand.lower())
        if len(flat_cand) >= _MIN_FLAT_LEN and flat_cand in flat_text:
            return True
    return False


def detect_target_app(
    request: str,
    history: list[dict] | None = None,
    apps: list[str] | None = None,
) -> str | None:
    """The connected app the user is talking about, if one is identifiable.

    The current request wins; the conversation is the fallback, so a bare
    answer ("tickets") to a clarifying question still resolves to the app
    that was being discussed. Returns None when it is genuinely ambiguous —
    the caller then leaves the choice to the model.
    """
    if not apps:
        return None
    names = _display_names(list(apps))
    candidates = {slug: _candidate_names(slug, names.get(slug, "")) for slug in apps}

    def matches(text: str) -> list[str]:
        return [slug for slug, cands in candidates.items() if cands and _mentions(text, cands)]

    request_hit = matches(request)
    if len(request_hit) == 1:
        return request_hit[0]

    history = history or []

    # The user's own words decide. PA's replies are not read for intent: if
    # PA happens to mention another app while answering, that must not
    # redirect the user's next message to it.
    for turn in reversed(history):
        if not isinstance(turn, dict) or turn.get("role") != "user":
            continue
        found = matches(str(turn.get("content") or ""))
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            # Genuinely ambiguous: do not guess.
            return None

    # No user turn named an app. Fall back to PA's own question, which is
    # what a short answer like "tickets" is replying to.
    for turn in reversed(history):
        if not isinstance(turn, dict) or turn.get("role") != "assistant":
            continue
        found = matches(str(turn.get("content") or ""))
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            return None

    return None


# --- Scope resolution ------------------------------------------------------


def app_display_name(slug: str) -> str:
    """A friendly name for an app slug ("hubspot" -> "HubSpot")."""
    return _display_name(slug)


def resolve_app_scope(
    request: str,
    history: list[dict] | None = None,
    apps: list[str] | None = None,
) -> dict:
    """Work out which apps this request may touch.

    Returns ``{"target": ..., "unconnected": ...}``:

    - ``target`` is the connected app the request is about, or None.
    - ``unconnected`` is an app the user named that they have *not*
      connected, or None. The assistant must not act on it, and saying so
      plainly beats inventing data for it.
    """
    connected = [a.lower() for a in (apps or [])]
    target = detect_target_app(request, history, connected)

    # Apps named anywhere in the exchange that the user cannot actually use.
    from pa.integrations import catalog

    parts = [request or ""]
    for turn in history or []:
        if isinstance(turn, dict):
            parts.append(str(turn.get("content") or ""))
    try:
        named = catalog.mentioned_apps(" \n ".join(parts))
    except Exception:
        named = []
    unconnected = next((s for s in named if s.lower() not in connected), None)
    return {"target": target, "unconnected": unconnected}


# --- Guarding what the assistant says --------------------------------------

# Words that mark a mention of an unconnected app as a *request to connect
# it* rather than a claim to have used it. Telling the user which app to
# connect is exactly what the assistant should do.
_CONNECT_HINTS = (
    "connect",
    "not connected",
    "would need",
    "you need",
    "you'll need",
    "need to",
    "available in",
    "install",
    "sign up",
    "link your",
    "authorise",
    "authorize",
    "apps section",
    "go to apps",
    "open the apps",
    "supported in",
    "only available",
)


def _sentences(text: str) -> list[str]:
    parts: list[str] = []
    for chunk in text.replace("\n", " ").split("."):
        chunk = chunk.strip()
        if chunk:
            parts.append(chunk)
    return parts or ([text] if text.strip() else [])


def guard_app_mentions(text: str, connected_slugs: list[str]) -> list[str]:
    """Apps the response talks about that the user has not connected.

    A mention is allowed when the assistant is asking the user to connect
    that app — that is helpful. It is a violation when the assistant speaks
    as if it had already used the app, because that is invented data.
    """
    if not text or not text.strip():
        return []
    from pa.integrations import catalog

    try:
        named = catalog.mentioned_apps(text)
    except Exception:
        return []
    connected = {s.lower() for s in connected_slugs}
    unconnected = [s for s in named if s.lower() not in connected]
    if not unconnected:
        return []

    violations: list[str] = []
    for sentence in _sentences(text):
        low = sentence.lower()
        if any(hint in low for hint in _CONNECT_HINTS):
            continue
        # Match the sentence with the same matcher used to find the mentions,
        # so a slug and its display name are treated alike.
        in_sentence = set(catalog.mentioned_apps(sentence))
        for slug in unconnected:
            if slug in in_sentence:
                violations.append(slug)
                break
    # Preserve order, no duplicates.
    seen: set[str] = set()
    return [s for s in violations if not (s in seen or seen.add(s))]
