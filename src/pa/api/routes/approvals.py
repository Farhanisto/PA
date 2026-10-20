"""Approval endpoints — authoritative stored proposal, no client payload swap."""

import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from pa.api.dependencies import get_current_user, get_db
from pa.db import repositories
from pa.schemas.assistant import ApprovalResponse
from pa.security import approvals as approvals_sec
from pa.security import audit as audit_mod

router = APIRouter()


@router.get(
    "/approvals",
    response_model=list[ApprovalResponse],
    summary="List my approvals",
    description="Newest first, scoped to the caller. Filter by status (default `pending`).",
)
def list_my_approvals(
    status_filter: str | None = Query(default="pending", alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """List the caller's approvals, newest first."""
    if status_filter is not None and status_filter not in ("pending", "approved", "rejected"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid status")
    approvals = repositories.list_approvals_for_user(
        db, user_id=user_id, status=status_filter, limit=limit
    )
    return [_to_response(a) for a in approvals]


def _to_response(approval) -> ApprovalResponse:
    try:
        payload = json.loads(approval.payload or "{}")
    except json.JSONDecodeError:
        payload = {}
    return ApprovalResponse(
        approval_id=approval.id,
        job_id=approval.job_id,
        thread_id=approval.thread_id,
        action=approval.action,
        payload=payload,
        status=approval.status,
    )


def _load_owned(db: Session, approval_id: str, user_id: str):
    approval = repositories.get_approval(db, approval_id)
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="approval not found")
    if approval.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not authorized")
    job = repositories.get_job(db, approval.job_id)
    if job is not None and job.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not authorized")
    return approval


@router.post(
    "/approvals/{approval_id}/approve",
    response_model=ApprovalResponse,
    summary="Approve a paused action",
    description=(
        "Approves the stored action proposal and enqueues workflow resumption "
        "on the same graph thread. The client cannot substitute a different "
        "payload — the stored proposal is authoritative. Duplicate approvals "
        "are rejected with 409."
    ),
)
def approve(
    approval_id: str, db: Session = Depends(get_db), user_id: str = Depends(get_current_user)
):
    """Approve a pending action and resume the paused workflow."""
    approval = _load_owned(db, approval_id, user_id)
    try:
        resolved = approvals_sec.decide(db, approval=approval, user_id=user_id, decision="approved")
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not authorized")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    audit_mod.record(
        db,
        event_type="approval.approved",
        user_id=user_id,
        job_id=approval.job_id,
        details={"approval_id": approval.id},
    )
    try:
        from pa.queue.tasks import resume_approved_workflow

        resume_approved_workflow.delay(approval.id)
    except Exception:
        pass
    return _to_response(resolved)


@router.post(
    "/approvals/{approval_id}/reject",
    response_model=ApprovalResponse,
    summary="Reject a paused action",
    description=(
        "Rejects the stored proposal. The paused job is marked failed with "
        "`approval_rejected` and no resume work is enqueued."
    ),
)
def reject(
    approval_id: str, db: Session = Depends(get_db), user_id: str = Depends(get_current_user)
):
    """Reject a pending action; the paused job is marked failed."""
    approval = _load_owned(db, approval_id, user_id)
    try:
        resolved = approvals_sec.decide(db, approval=approval, user_id=user_id, decision="rejected")
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not authorized")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    audit_mod.record(
        db,
        event_type="approval.rejected",
        user_id=user_id,
        job_id=approval.job_id,
        details={"approval_id": approval.id},
    )
    # Mark job failed (rejected) so status is visible; no resume enqueued.
    job = repositories.get_job(db, approval.job_id)
    if job is not None and job.status == "waiting_for_approval":
        repositories.update_job(
            db, job, status="failed", error_code="approval_rejected", finished=True
        )
    return _to_response(resolved)
