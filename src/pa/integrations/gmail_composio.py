"""Per-user Gmail access through Composio.

Composio owns the OAuth connection and holds the refresh token, so PA never
stores user mail credentials. Every function takes the *user_id* and looks up
that user's own connected account: one user's mailbox is never reachable from
another's request.

There is no offline/demo transport. If Composio is unconfigured, or the user has
not connected Gmail, these functions raise :class:`IntegrationNotReady` so the
caller can surface an honest "connect your app first" message rather than
inventing data.
"""

import logging
from typing import Any, Optional

from pa.integrations import composio_client

logger = logging.getLogger(__name__)

PROVIDER = "gmail"

# Gmail tool slugs on Composio. Override via env if the account differs.
TOOL_FETCH = "GMAIL_FETCH_EMAILS"
TOOL_SEND = "GMAIL_SEND_EMAIL"


class IntegrationNotReady(RuntimeError):
    """Composio is unconfigured, or the user has not approved this app."""


def _tool_fetch() -> str:
    import os

    return os.getenv("COMPOSIO_GMAIL_FETCH_TOOL", TOOL_FETCH)


def _tool_send() -> str:
    import os

    return os.getenv("COMPOSIO_GMAIL_SEND_TOOL", TOOL_SEND)


def is_enabled() -> bool:
    """True when a Composio client is reachable (key configured, or test double)."""
    return composio_client.is_configured()


def _connected_account_id(user_id: str, client) -> Optional[str]:
    """Resolve the caller's active Gmail connected-account id."""
    resp = client.client.connected_accounts.list(
        user_ids=[composio_client.user_ref(user_id)], toolkit_slugs=["gmail"], limit=10
    )
    items = getattr(resp, "items", None) or getattr(resp, "data", None) or []
    if isinstance(items, dict):
        items = items.get("items", []) or items.get("data", [])
    for item in items:
        if composio_client._norm_status(item) == "ACTIVE":
            cid = composio_client._pick_id(item)
            if cid:
                return cid
    return None


def _require_configured() -> None:
    """Refuse up front when Composio is unusable, before building a client."""
    if not is_enabled():
        raise IntegrationNotReady(
            "Composio is not configured. Set COMPOSIO_API_KEY on the API server."
        )


def _require_account(user_id: str, client) -> str:
    """Return the caller's active account id, or explain why we cannot proceed."""
    _require_configured()
    account_id = _connected_account_id(user_id, client)
    if not account_id:
        raise IntegrationNotReady(
            "Gmail is not connected. Connect Gmail in Apps, approve access, then retry."
        )
    return account_id


def _coerce(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize Composio's several inbox shapes into our dict form."""
    messages: list[dict[str, Any]] = []
    payload = raw
    for key in ("data", "result", "messages", "value"):
        if isinstance(payload, dict) and key in payload:
            payload = payload[key]
            break
    if isinstance(payload, dict):
        payload = payload.get("messages", []) or payload.get("data", []) or []
    if not isinstance(payload, list):
        return messages
    for item in payload[:50]:
        if not isinstance(item, dict):
            continue
        # Gmail API nests the real header under "payload.headers".
        headers: dict[str, str] = {}
        payload_obj = item.get("payload") or {}
        if isinstance(payload_obj, dict):
            for h in payload_obj.get("headers") or []:
                if isinstance(h, dict) and h.get("name"):
                    headers[str(h["name"]).lower()] = str(h.get("value", ""))
        sender = item.get("from") or headers.get("from") or ""
        if not sender and isinstance(item.get("sender"), dict):
            sender = item["sender"].get("emailAddress", "") or item["sender"].get("name", "")
        messages.append(
            {
                "id": str(item.get("id") or item.get("messageId") or ""),
                "threadId": str(item.get("threadId") or item.get("thread_id") or ""),
                "from": str(sender)[:320],
                "subject": str(item.get("subject") or headers.get("subject") or "")[:998],
                "snippet": str(item.get("snippet") or item.get("bodyPreview") or "")[:2000],
                "body": str(item.get("body") or item.get("bodyText") or item.get("snippet") or "")[
                    :20000
                ],
            }
        )
    return messages


def fetch_inbox(user_id: str, *, max_results: int = 20, client=None) -> list[dict[str, Any]]:
    """Fetch the caller's real inbox messages.

    Raises IntegrationNotReady when the app is not configured or not connected.
    An empty list means a genuinely empty inbox.
    """
    max_results = max(1, min(int(max_results), 50))
    _require_configured()
    client = client or composio_client.get_client()
    account_id = _require_account(user_id, client)
    try:
        resp = client.client.tools.execute(
            tool_slug=_tool_fetch(),
            arguments={"maxResults": max_results, "q": "in:inbox"},
            connected_account_id=account_id,
        )
    except Exception as exc:
        raise RuntimeError(f"Gmail fetch failed: {type(exc).__name__}: {exc}") from exc
    payload = resp if isinstance(resp, dict) else getattr(resp, "data", {}) or {}
    return _coerce(payload)


def send_email(
    user_id: str,
    *,
    to: str,
    subject: str,
    body: str,
    in_reply_to: str = "",
    client=None,
) -> dict[str, Any]:
    """Send a reply through the caller's connected Gmail account."""
    to = (to or "").strip()
    subject = (subject or "").strip()
    body = (body or "").strip()
    if not to or "@" not in to:
        raise ValueError("valid recipient email required")
    if not body:
        raise ValueError("body must not be empty")
    if len(body) > 100000:
        raise ValueError("body too long")

    _require_configured()
    client = client or composio_client.get_client()
    account_id = _require_account(user_id, client)
    payload: dict[str, Any] = {"to": to, "subject": subject, "body": body}
    if in_reply_to:
        payload["thread_id"] = in_reply_to
    try:
        resp = client.client.tools.execute(
            tool_slug=_tool_send(),
            arguments=payload,
            connected_account_id=account_id,
        )
    except Exception as exc:
        raise RuntimeError(f"Gmail send failed: {type(exc).__name__}: {exc}") from exc
    data = resp if isinstance(resp, dict) else getattr(resp, "data", {}) or {}
    return {"id": str(data.get("id") or data.get("messageId") or "sent")}


def connection_state(user_id: str) -> str:
    if not is_enabled():
        return "unavailable"
    return composio_client.connection_state(user_id, PROVIDER)