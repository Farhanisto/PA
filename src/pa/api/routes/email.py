"""Email routes: inbox triage, drafts, and approval-gated sending.

Every handler derives the user from the session (never from the request
body), and every repository call filters by that user id.
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from pa.api.dependencies import get_current_user, get_db
from pa.auth import PERM_READ_INBOX, PERM_SEND_EMAIL, permission_granted
from pa.db.models import EmailDraft, EmailMessage
from pa.schemas.auth import (
    DraftCreateRequest,
    DraftGenerateRequest,
    DraftResponse,
    DraftUpdateRequest,
    EmailMessageResponse,
    TriageResponse,
)
from pa.services import email_service as svc
from pa.services import tracing
from pa.services.email_service import EmailError

router = APIRouter(prefix="/email", tags=["email"])


def _msg_response(m: EmailMessage) -> EmailMessageResponse:
    return EmailMessageResponse(
        id=m.id,
        provider_message_id=m.provider_message_id,
        from_addr=m.from_addr,
        subject=m.subject,
        snippet=m.snippet,
        received_at=m.received_at,
        summary=m.summary,
        priority=m.priority,
        needs_reply=m.needs_reply,
        action_items=svc._as_json_list(m.action_items),
        deadlines=svc._as_json_list(m.deadlines),
        triaged=m.triaged_at is not None,
    )


def _draft_response(d: EmailDraft) -> DraftResponse:
    return DraftResponse(
        id=d.id,
        message_id=d.message_id,
        in_reply_to=d.in_reply_to or "",
        to_addr=d.to_addr,
        subject=d.subject,
        body=d.body,
        status=d.status,
        created_at=d.created_at,
        updated_at=d.updated_at,
        sent_at=d.sent_at,
        sent_message_id=d.sent_message_id,
    )


def _http_error(exc: EmailError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post(
    "/sync",
    response_model=TriageResponse,
    summary="Fetch and triage the inbox",
    description="Pulls recent messages for the caller, then summarizes/prioritizes each one and extracts deadlines and follow-ups.",
)
def sync_inbox(
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """Sync + triage the caller's inbox."""
    if not permission_granted(db, user_id=user_id, key=PERM_READ_INBOX):
        raise HTTPException(status_code=403, detail="inbox read permission denied")
    job_id = f"sync-{uuid.uuid4().hex[:12]}"
    with tracing.trace_tool(
        db,
        user_id=user_id,
        job_id=job_id,
        tool_name="gmail_fetch_inbox",
        args={"limit": limit},
    ) as rec:
        fetched, triaged = svc.sync_inbox(
            db, user_id=user_id, max_results=limit, job_id=job_id
        )
        rec["output"] = {"fetched": fetched, "triaged": triaged}
    msgs = svc.list_messages(db, user_id=user_id, limit=limit)
    return TriageResponse(fetched=fetched, triaged=triaged, message_ids=[m.id for m in msgs])


@router.get(
    "/messages",
    response_model=list[EmailMessageResponse],
    summary="List triaged messages",
)
def list_messages(
    priority: str | None = Query(default=None),
    needs_reply: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    if not permission_granted(db, user_id=user_id, key=PERM_READ_INBOX):
        raise HTTPException(status_code=403, detail="inbox read permission denied")
    msgs = svc.list_messages(
        db, user_id=user_id, priority=priority, needs_reply=needs_reply, limit=limit
    )
    return [_msg_response(m) for m in msgs]


@router.get(
    "/messages/{message_id}", response_model=EmailMessageResponse, summary="Get one message"
)
def get_message(
    message_id: str, db: Session = Depends(get_db), user_id: str = Depends(get_current_user)
):
    try:
        return _msg_response(svc.get_message(db, user_id=user_id, message_id=message_id))
    except EmailError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get(
    "/followups",
    response_model=list[EmailMessageResponse],
    summary="Messages needing a reply",
    description="Drives proactive follow-up reminders (requirement §6).",
)
def followups(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    if not permission_granted(db, user_id=user_id, key=PERM_READ_INBOX):
        raise HTTPException(status_code=403, detail="inbox read permission denied")
    return [_msg_response(m) for m in svc.needs_followup(db, user_id=user_id, limit=limit)]


# --- Drafts ---


@router.post("/drafts", response_model=DraftResponse, status_code=201, summary="Create a draft")
def create_draft(
    body: DraftCreateRequest,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    try:
        draft = svc.create_draft(
            db,
            user_id=user_id,
            to_addr=body.to_addr,
            subject=body.subject,
            body=body.body,
            message_id=body.message_id,
            in_reply_to=body.in_reply_to,
        )
    except EmailError as exc:
        raise _http_error(exc)
    return _draft_response(draft)


@router.post(
    "/drafts/generate",
    response_model=DraftResponse,
    status_code=201,
    summary="Generate a reply draft",
    description="Drafts a contextual reply to an existing message for the caller to review and edit.",
)
def generate_draft(
    body: DraftGenerateRequest,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    job_id = f"draft-{uuid.uuid4().hex[:12]}"
    with tracing.trace_tool(
        db,
        user_id=user_id,
        job_id=job_id,
        tool_name="email_draft_reply",
        args={"message_id": body.message_id},
    ) as rec:
        try:
            draft = svc.generate_draft(
                db, user_id=user_id, message_id=body.message_id, instructions=body.instructions
            )
        except EmailError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        rec["output"] = {"draft_id": draft.id, "subject": draft.subject}
    return _draft_response(draft)


@router.get("/drafts", response_model=list[DraftResponse], summary="List my drafts")
def list_drafts(
    status_filter: str | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    return [_draft_response(d) for d in svc.list_drafts(db, user_id=user_id, status=status_filter)]


@router.get("/drafts/{draft_id}", response_model=DraftResponse, summary="Get a draft")
def get_draft(
    draft_id: str, db: Session = Depends(get_db), user_id: str = Depends(get_current_user)
):
    try:
        return _draft_response(svc.get_draft(db, user_id=user_id, draft_id=draft_id))
    except EmailError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.patch("/drafts/{draft_id}", response_model=DraftResponse, summary="Edit a draft")
def update_draft(
    draft_id: str,
    body: DraftUpdateRequest,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    try:
        draft = svc.update_draft(
            db,
            user_id=user_id,
            draft_id=draft_id,
            subject=body.subject,
            body=body.body,
            to_addr=body.to_addr,
        )
    except EmailError as exc:
        raise _http_error(exc)
    return _draft_response(draft)


@router.delete(
    "/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a draft"
)
def delete_draft(
    draft_id: str, db: Session = Depends(get_db), user_id: str = Depends(get_current_user)
):
    try:
        svc.delete_draft(db, user_id=user_id, draft_id=draft_id)
    except EmailError as exc:
        raise _http_error(exc)
    return None


@router.post(
    "/drafts/{draft_id}/send-request",
    response_model=dict,
    summary="Request approval to send a draft",
    description=(
        "Creates an approval record for the draft. Sending happens only after an "
        "explicit approval (requirement §5)."
    ),
)
def request_send(
    draft_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    from pa.db import repositories
    from pa.db.models import Approval
    from pa.security import audit as audit_mod

    if not permission_granted(db, user_id=user_id, key=PERM_SEND_EMAIL):
        raise HTTPException(status_code=403, detail="send permission denied")
    try:
        draft = svc.get_draft(db, user_id=user_id, draft_id=draft_id)
    except EmailError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if draft.status == "sent":
        raise HTTPException(status_code=409, detail="draft already sent")

    # Idempotent: reuse an existing pending approval for this draft.
    # (Filter in Python — a SQL LIKE/JSON operator would be backend-specific.)
    for cand in repositories.list_approvals_for_user(
        db, user_id=user_id, status="pending", limit=100
    ):
        if cand.action == "email_send_draft":
            try:
                if json.loads(cand.payload or "{}").get("draft_id") == draft.id:
                    return {"approval_id": cand.id, "status": "pending", "draft_id": draft.id}
            except json.JSONDecodeError:
                continue

    job = repositories.create_job(
        db, user_id=user_id, request=f"Send draft {draft.id[:8]} to {draft.to_addr}"
    )
    approval = repositories.create_approval(
        db,
        user_id=user_id,
        job_id=job.id,
        thread_id=job.thread_id,
        action="email_send_draft",
        payload={
            "draft_id": draft.id,
            "to_addr": draft.to_addr,
            "subject": draft.subject,
            "in_reply_to": draft.in_reply_to,
        },
    )
    audit_mod.record(
        db,
        event_type="email.send_requested",
        user_id=user_id,
        job_id=job.id,
        details={"draft_id": draft.id, "approval_id": approval.id},
    )
    db.commit()
    return {"approval_id": approval.id, "status": "pending", "draft_id": draft.id}
