"""Structured logging with job/thread/correlation IDs and secret redaction.

Per instructions §16: every request/task carries job_id, thread_id,
correlation ID. Never log API keys, auth headers, or private content.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from contextvars import ContextVar
from typing import Any

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key\s*[:=]\s*)\S+|(authorization\s*:\s*Bearer\s+)\S+|(bearer\s+)\S+"
)


def get_correlation_id() -> str:
    cid = _correlation_id.get()
    if not cid:
        cid = str(uuid.uuid4())
        _correlation_id.set(cid)
    return cid


def set_correlation_id(cid: str | None) -> None:
    _correlation_id.set(cid or str(uuid.uuid4()))


def redact_value(text: str) -> str:
    return _SECRET_RE.sub(lambda m: next(g for g in m.groups() if g) + "[REDACTED]", text)


def redact_obj(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: "[REDACTED]"
            if any(
                s in str(k).lower()
                for s in ("api_key", "apikey", "authorization", "token", "password", "secret")
            )
            else redact_obj(v)
            for k, v in obj.items()
        }
    if isinstance(obj, str):
        return redact_value(obj)
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    return obj


def log_event(
    logger: logging.Logger,
    level: int,
    message: str,
    *,
    job_id: str | None = None,
    thread_id: str | None = None,
    user_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "correlation_id": get_correlation_id(),
        "job_id": job_id,
        "thread_id": thread_id,
        "user_id": user_id,
    }
    if extra:
        payload.update(redact_obj(extra))  # type: ignore[arg-type]
    # Drop Nones for compactness, keep correlation always.
    clean = {k: v for k, v in payload.items() if v is not None}
    logger.log(level, "%s | %s", message, json.dumps(clean, default=str))
