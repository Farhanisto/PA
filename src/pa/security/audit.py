"""Audit logging with secret redaction."""

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from pa.db import repositories

_SECRET_KEYS = ("api_key", "apikey", "authorization", "bearer", "token", "password", "secret")
_SECRET_RE = re.compile(r"(?i)(api[_-]?key|authorization|bearer)\s*[:=]\s*\S+")


def redact(details: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for k, v in details.items():
        lk = str(k).lower()
        if any(s in lk for s in _SECRET_KEYS):
            clean[k] = "[REDACTED]"
            continue
        if isinstance(v, str):
            # If the value itself looks like a credential/bearer token, redact whole value.
            if re.search(r"(?i)(bearer\s+\S+|api[_-]?key|authorization)", v):
                clean[k] = "[REDACTED]"
            else:
                clean[k] = _SECRET_RE.sub(r"\1=[REDACTED]", v)
        elif isinstance(v, dict):
            clean[k] = redact(v)
        else:
            clean[k] = v
    return clean


def record(
    db: Session,
    *,
    event_type: str,
    user_id: str | None = None,
    job_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    repositories.create_audit_event(
        db, event_type=event_type, user_id=user_id, job_id=job_id, details=redact(details or {})
    )


def details_to_dict(evt) -> dict[str, Any]:
    try:
        return json.loads(evt.details or "{}")
    except json.JSONDecodeError:
        return {}
