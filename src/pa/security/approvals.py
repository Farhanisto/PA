"""Approval helpers: ownership, staleness, idempotency."""

import json
from typing import Any

from sqlalchemy.orm import Session

from pa.db import repositories
from pa.db.models import Approval


def verify_ownership(approval: Approval, user_id: str) -> None:
    if approval.user_id != user_id:
        raise PermissionError("not authorized for this approval")


def get_payload(approval: Approval) -> dict[str, Any]:
    try:
        return json.loads(approval.payload or "{}")
    except json.JSONDecodeError:
        return {}


def decide(db: Session, *, approval: Approval, user_id: str, decision: str) -> Approval:
    """Persist approval decision; rejects duplicates/stale submissions."""
    verify_ownership(approval, user_id)
    if approval.status != "pending":
        raise ValueError("approval already resolved (duplicate or stale)")
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be approved or rejected")
    # NOTE: we intentionally ignore any client-supplied replacement payload.
    # The stored proposal is authoritative (see routes/approvals.py).
    return repositories.resolve_approval(db, approval, status=decision)
