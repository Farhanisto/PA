"""Making model failures actionable.

A provider failure arrives as an opaque ``APIStatusError``. Without the
status code it is impossible to tell a bad key from a rate limit, and
impossible to tell a retryable problem from a permanent one.

Two things matter:

- **The status code**, so the caller can decide whether to retry.
- **A sentence a person can act on**, because the raw provider message is
  rarely comprehensible to the user waiting for an answer.
"""

from __future__ import annotations

import re
from typing import Any

# Statuses worth retrying: the provider is busy, rate limiting, or briefly
# broken. 413 is included because it is a per-minute token budget, so the
# same request usually succeeds moments later.
TRANSIENT_STATUS = frozenset({408, 409, 413, 425, 429, 500, 502, 503, 504, 529})

_STATUS_RE = re.compile(r"status(?:_code)?\s*[=:]\s*(\d{3})", re.IGNORECASE)
_CODE_RE = re.compile(r"[Ee]rror code:\s*(\d{3})")


def status_of(exc: Any) -> int | None:
    """The HTTP status behind an exception, if there is one."""
    for attr in ("status_code", "status", "http_status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and 100 <= value < 600:
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    if isinstance(value, int) and 100 <= value < 600:
        return value
    # Fall back to the message: the openai SDK formats it as
    # "Error code: 429 - {...}".
    text = str(exc)
    match = _CODE_RE.search(text) or _STATUS_RE.search(text)
    if match:
        return int(match.group(1))
    return None


def describe(exc: Exception) -> str:
    """A one-line description of a model failure, safe to show a user."""
    name = type(exc).__name__
    status = status_of(exc)
    detail = str(exc).strip().replace("\n", " ")
    if len(detail) > 300:
        detail = detail[:300] + "…"
    if status:
        return f"model_error: {name}: status={status}: {detail}"
    return f"model_error: {name}: {detail}"


def is_transient(exc: Exception) -> bool:
    """Is this failure worth retrying?"""
    status = status_of(exc)
    if status is not None:
        return status in TRANSIENT_STATUS
    text = str(exc).lower()
    markers = (
        "timeout",
        "timed out",
        "connection",
        "temporarily",
        "service unavailable",
        "rate limit",
        "too large",
        "overloaded",
    )
    return any(m in text for m in markers)


# What the user is told, per status. Actionable, and never a raw provider
# message.
FRIENDLY: dict[int, str] = {
    400: "The model rejected the request. It may be too long — try a shorter question.",
    401: "The model provider rejected the API key. Check it in .env and restart the API.",
    403: "The model provider denied this request. Check the key's permissions.",
    413: "The request was too large for the model. Try asking about fewer records at once.",
    422: "The model could not process the request. Try rephrasing it.",
    429: "The model provider is rate limiting. Wait a moment and try again.",
    500: "The model provider had an internal error. Try again shortly.",
    502: "The model provider is unreachable right now. Try again shortly.",
    503: "The model provider is overloaded. Try again shortly.",
    504: "The model provider timed out. Try again shortly.",
}


def friendly(exc: Exception) -> str:
    """A sentence the user can act on."""
    status = status_of(exc)
    if status in FRIENDLY:
        return FRIENDLY[status]
    return "The assistant could not reach the model. Try again in a moment."
