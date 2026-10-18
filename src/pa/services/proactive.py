"""Proactive assistance: scheduled inbox briefings and follow-up reminders (req §6).

Briefings are *proposed*, never sent. They surface in the app and can be
turned into an approval-gated send, so the assistant can work ahead of the
user without ever taking an external action unattended.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from pa.db.models import Memory
from pa.services import email_service as email_svc

MAX_ITEMS = 8


def build_inbox_briefing(db: Session, *, user_id: str, job_id: str = "") -> dict[str, Any]:
    """Compose a triage summary of the caller's inbox."""
    # Refresh from the provider so the briefing reflects new mail.
    try:
        fetched, triaged = email_svc.sync_inbox(
            db, user_id=user_id, max_results=25, job_id=job_id
        )
    except Exception as exc:  # degrade to cached data rather than failing the briefing
        fetched, triaged = 0, 0
        sync_error = f"{type(exc).__name__}: {exc}"
    else:
        sync_error = ""

    msgs = email_svc.list_messages(db, user_id=user_id, limit=50)
    high = [m for m in msgs if m.priority == "high"]
    normal = [m for m in msgs if m.priority == "normal"]
    low = [m for m in msgs if m.priority == "low"]
    followups = [m for m in msgs if m.needs_reply]

    deadlines: list[str] = []
    for m in msgs:
        for d in email_svc._as_json_list(m.deadlines):
            if d not in deadlines:
                deadlines.append(d)

    prefs = db.scalars(
        select(Memory).where(Memory.user_id == user_id, Memory.category == "preference").limit(5)
    ).all()

    lines: list[str] = ["Inbox Briefing", "=" * 15, ""]
    lines.append(f"{len(msgs)} triaged · {fetched} new")
    if sync_error:
        lines.append(f"(sync warning: {sync_error[:120]})")
    lines.append("")

    if high:
        lines.append(f"HIGH PRIORITY ({len(high)}):")
        for m in high[:MAX_ITEMS]:
            lines.append(f"  • {m.subject or '(no subject)'}")
            lines.append(f"    from {m.from_addr}")
            if m.summary:
                lines.append(f"    {m.summary[:200]}")
        lines.append("")
    if deadlines:
        lines.append("DEADLINES:")
        for d in deadlines[:MAX_ITEMS]:
            lines.append(f"  ⏰ {d}")
        lines.append("")
    if followups:
        lines.append(f"NEEDS YOUR REPLY ({len(followups)}):")
        for m in followups[:MAX_ITEMS]:
            lines.append(f"  ✉️  {m.subject or '(no subject)'} — from {m.from_addr}")
        lines.append("")
    if normal:
        lines.append(f"Normal ({len(normal)}) — summarize on demand.")
    if low:
        lines.append(f"Low priority / newsletters ({len(low)}) — safely ignorable.")
    if not msgs:
        lines.append("Inbox is empty (or not connected yet).")
    if prefs:
        lines.append("")
        lines.append("Your preferences:")
        for p in prefs:
            lines.append(f"  - {p.content[:120]}")

    return {
        "text": "\n".join(lines),
        "counts": {
            "total": len(msgs),
            "high": len(high),
            "normal": len(normal),
            "low": len(low),
            "needs_reply": len(followups),
            "deadlines": len(deadlines),
        },
        "fetched": fetched,
        "triaged": triaged,
    }


def build_followup_reminders(db: Session, *, user_id: str, limit: int = 20) -> dict[str, Any]:
    """List messages still awaiting the user's reply, oldest first."""
    msgs = email_svc.needs_followup(db, user_id=user_id, limit=limit)
    msgs = sorted(msgs, key=lambda m: m.received_at or m.created_at)
    lines = ["Follow-up Reminders", "=" * 18, ""]
    if not msgs:
        lines.append("Nothing is waiting on you. Nice.")
    else:
        lines.append(f"{len(msgs)} message(s) awaiting your reply:")
        for i, m in enumerate(msgs, 1):
            subject = m.subject or "(no subject)"
            lines.append(f"  {i}. {subject}")
            lines.append(f"     from {m.from_addr}")
            if m.summary:
                lines.append(f"     {m.summary[:180]}")
            dl = email_svc._as_json_list(m.deadlines)
            if dl:
                lines.append(f"     deadline: {dl[0]}")
    return {"text": "\n".join(lines), "count": len(msgs)}
