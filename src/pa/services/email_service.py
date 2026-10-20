"""Email service: inbox cache, triage application, drafts, and gated sends.

All functions take an explicit ``user_id`` and filter by it at the query
level, so a missing/incorrect ownership check is a bug, not a data leak.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pa.db.models import AuditEvent, EmailDraft, EmailMessage
from pa.integrations import gmail_composio
from pa.services.email_triage import TriageResult, draft_reply, triage_message

logger = logging.getLogger(__name__)

DRAFT_STATUSES = ("draft", "approved", "sent", "rejected", "failed")


class EmailError(Exception):
    """Email-domain failure safe to surface to the caller."""


def _audit(db: Session, *, user_id: str, event_type: str, **details: Any) -> None:
    db.add(AuditEvent(user_id=user_id, event_type=event_type, details=json.dumps(details)))


def _model_invoker() -> Optional[Callable[[str], str]]:
    """Return a prompt->text callable for the configured model, or None."""
    try:
        from pa.queue.tasks import _resolve_task_model

        model = _resolve_task_model()
    except Exception:
        return None
    if model is None:
        return None

    def _invoke(prompt: str) -> str:
        result = model.invoke(prompt) if hasattr(model, "invoke") else model(prompt)
        return getattr(result, "content", result)

    return _invoke


# --- Inbox + triage ---


def sync_inbox(
    db: Session,
    *,
    user_id: str,
    max_results: int = 20,
    job_id: str = "",
    use_model: bool = True,
) -> tuple[int, int]:
    """Fetch the caller's inbox, upsert messages, and triage new ones.

    Returns (fetched, triaged). Raises IntegrationNotReady when the caller has
    not connected the app, rather than substituting placeholder mail.
    """
    raw = gmail_composio.fetch_inbox(user_id, max_results=max_results)
    invoke = _model_invoker() if use_model else None
    fetched = 0
    triaged = 0

    for item in raw:
        provider_id = str(item.get("id") or "").strip()
        if not provider_id:
            continue
        fetched += 1
        existing = db.scalars(
            select(EmailMessage).where(
                EmailMessage.user_id == user_id,
                EmailMessage.provider_message_id == provider_id,
            )
        ).first()

        if existing is None:
            msg = EmailMessage(
                user_id=user_id,
                provider_message_id=provider_id,
                thread_id=str(item.get("threadId") or "")[:255],
                from_addr=str(item.get("from") or "")[:320],
                subject=str(item.get("subject") or "")[:998],
                snippet=str(item.get("snippet") or "")[:4000],
                body=str(item.get("body") or "")[:20000],
            )
            db.add(msg)
            try:
                db.flush()
            except IntegrityError:
                # Concurrent fetch inserted it first; skip this one.
                db.rollback()
                continue
            needs_triage = True
        else:
            msg = existing
            needs_triage = msg.triaged_at is None

        if needs_triage:
            result: TriageResult = triage_message(
                {
                    "subject": msg.subject,
                    "from": msg.from_addr,
                    "snippet": msg.snippet,
                    "body": msg.body,
                },
                model_invoke=invoke,
            )
            msg.summary = result.summary
            msg.priority = result.priority
            msg.needs_reply = result.needs_reply
            msg.action_items = json.dumps(result.action_items)
            msg.deadlines = json.dumps(result.deadlines)
            msg.triaged_at = datetime.now(UTC)
            triaged += 1

    _audit(
        db,
        user_id=user_id,
        event_type="email.inbox_synced",
        job_id=job_id,
        fetched=fetched,
        triaged=triaged,
    )
    db.commit()
    return fetched, triaged


def _as_json_list(raw: str) -> list[str]:
    try:
        val = json.loads(raw or "[]")
        return val if isinstance(val, list) else []
    except json.JSONDecodeError:
        return []


def list_messages(
    db: Session,
    *,
    user_id: str,
    priority: str | None = None,
    needs_reply: bool | None = None,
    limit: int = 50,
) -> list[EmailMessage]:
    stmt = select(EmailMessage).where(EmailMessage.user_id == user_id)
    if priority:
        stmt = stmt.where(EmailMessage.priority == priority)
    if needs_reply is not None:
        stmt = stmt.where(EmailMessage.needs_reply.is_(needs_reply))
    stmt = stmt.order_by(EmailMessage.created_at.desc()).limit(max(1, min(limit, 200)))
    return list(db.scalars(stmt).all())


def get_message(db: Session, *, user_id: str, message_id: str) -> EmailMessage:
    msg = db.get(EmailMessage, message_id)
    if msg is None or msg.user_id != user_id:
        # Same response for missing and not-yours: no existence oracle.
        raise EmailError("message not found")
    return msg


def count_by_priority(db: Session, *, user_id: str) -> dict[str, int]:
    from sqlalchemy import func

    rows = db.execute(
        select(EmailMessage.priority, func.count(EmailMessage.id))
        .where(EmailMessage.user_id == user_id)
        .group_by(EmailMessage.priority)
    ).all()
    return {str(p): int(c) for p, c in rows}


def needs_followup(db: Session, *, user_id: str, limit: int = 20) -> list[EmailMessage]:
    """Messages triaged as requiring a reply — the follow-up reminder source."""
    return list_messages(db, user_id=user_id, needs_reply=True, limit=limit)


# --- Drafts ---


def create_draft(
    db: Session,
    *,
    user_id: str,
    to_addr: str,
    subject: str = "",
    body: str = "",
    message_id: str | None = None,
    in_reply_to: str = "",
) -> EmailDraft:
    if message_id:
        # Validates ownership (raises EmailError otherwise).
        get_message(db, user_id=user_id, message_id=message_id)
    to_addr = (to_addr or "").strip()
    if not to_addr or "@" not in to_addr:
        raise EmailError("valid recipient email required")
    if not (body or "").strip():
        raise EmailError("draft body must not be empty")
    draft = EmailDraft(
        user_id=user_id,
        message_id=message_id,
        in_reply_to=(in_reply_to or "")[:255],
        to_addr=to_addr[:320],
        subject=(subject or "")[:998],
        body=body[:100000],
        status="draft",
    )
    db.add(draft)
    _audit(db, user_id=user_id, event_type="email.draft_created", message_id=message_id)
    db.commit()
    db.refresh(draft)
    return draft


def generate_draft(
    db: Session,
    *,
    user_id: str,
    message_id: str,
    instructions: str = "",
) -> EmailDraft:
    """Model-generated reply for a message the caller owns."""
    msg = get_message(db, user_id=user_id, message_id=message_id)
    invoke = _model_invoker()
    body = draft_reply(
        {
            "subject": msg.subject,
            "from": msg.from_addr,
            "snippet": msg.snippet,
            "body": msg.body,
        },
        instructions=instructions,
        model_invoke=invoke,
    )
    reply_subject = (
        msg.subject if msg.subject.lower().startswith("re:") else f"Re: {msg.subject}".strip()
    )
    draft = create_draft(
        db,
        user_id=user_id,
        to_addr=_extract_email(msg.from_addr),
        subject=reply_subject,
        body=body,
        message_id=message_id,
        in_reply_to=msg.thread_id or msg.provider_message_id,
    )
    _audit(
        db,
        user_id=user_id,
        event_type="email.draft_generated",
        draft_id=draft.id,
        message_id=message_id,
        model=bool(invoke),
    )
    db.commit()
    return draft


def _extract_email(addr: str) -> str:
    import re

    match = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", addr or "")
    return match.group(0) if match else (addr or "").strip()


def list_drafts(
    db: Session, *, user_id: str, status: str | None = None, limit: int = 50
) -> list[EmailDraft]:
    stmt = select(EmailDraft).where(EmailDraft.user_id == user_id)
    if status:
        stmt = stmt.where(EmailDraft.status == status)
    stmt = stmt.order_by(EmailDraft.updated_at.desc()).limit(max(1, min(limit, 200)))
    return list(db.scalars(stmt).all())


def get_draft(db: Session, *, user_id: str, draft_id: str) -> EmailDraft:
    draft = db.get(EmailDraft, draft_id)
    if draft is None or draft.user_id != user_id:
        raise EmailError("draft not found")
    return draft


def update_draft(
    db: Session,
    *,
    user_id: str,
    draft_id: str,
    subject: str | None = None,
    body: str | None = None,
    to_addr: str | None = None,
) -> EmailDraft:
    draft = get_draft(db, user_id=user_id, draft_id=draft_id)
    if draft.status in ("sent", "rejected"):
        raise EmailError(f"cannot edit a {draft.status} draft")
    if subject is not None:
        draft.subject = subject[:998]
    if body is not None:
        if not body.strip():
            raise EmailError("draft body must not be empty")
        draft.body = body[:100000]
    if to_addr is not None:
        if "@" not in to_addr:
            raise EmailError("valid recipient email required")
        draft.to_addr = to_addr[:320]
    _audit(db, user_id=user_id, event_type="email.draft_updated", draft_id=draft.id)
    db.commit()
    db.refresh(draft)
    return draft


def delete_draft(db: Session, *, user_id: str, draft_id: str) -> None:
    draft = get_draft(db, user_id=user_id, draft_id=draft_id)
    if draft.status == "sent":
        raise EmailError("cannot delete a sent draft")
    db.delete(draft)
    _audit(db, user_id=user_id, event_type="email.draft_deleted", draft_id=draft_id)
    db.commit()


def mark_draft_sent(
    db: Session, *, user_id: str, draft_id: str, provider_message_id: str
) -> EmailDraft:
    draft = get_draft(db, user_id=user_id, draft_id=draft_id)
    draft.status = "sent"
    draft.sent_at = datetime.now(UTC)
    draft.sent_message_id = provider_message_id[:255]
    _audit(
        db,
        user_id=user_id,
        event_type="email.sent",
        draft_id=draft.id,
        to_addr=draft.to_addr,
        provider_message_id=provider_message_id[:255],
    )
    db.commit()
    db.refresh(draft)
    return draft


def send_draft(db: Session, *, user_id: str, draft_id: str) -> EmailDraft:
    """Actually send a draft. Callers MUST have enforced approval + permission."""
    draft = get_draft(db, user_id=user_id, draft_id=draft_id)
    if draft.status == "sent":
        raise EmailError("draft already sent")
    if draft.status == "rejected":
        raise EmailError("draft was rejected")
    result = gmail_composio.send_email(
        user_id,
        to=draft.to_addr,
        subject=draft.subject,
        body=draft.body,
        in_reply_to=draft.in_reply_to,
    )
    return mark_draft_sent(
        db, user_id=user_id, draft_id=draft.id, provider_message_id=str(result.get("id", ""))
    )
