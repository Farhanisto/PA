"""Repository functions so routes/tasks never contain raw SQL."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from pa.db.models import Approval, AuditEvent, Job, Memory

JOB_STATUSES = {"queued", "running", "waiting_for_approval", "completed", "failed", "cancelled"}
APPROVAL_STATUSES = {"pending", "approved", "rejected"}


def _utcnow() -> datetime:
    return datetime.now(UTC)


# --- Jobs ---


def create_job(db: Session, *, user_id: str, request: str, thread_id: str | None = None) -> Job:
    job = Job(
        id=str(uuid.uuid4()),
        user_id=user_id,
        thread_id=thread_id or str(uuid.uuid4()),
        status="queued",
        request=request,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_job(db: Session, job_id: str) -> Job | None:
    return db.get(Job, job_id)


def get_job_by_thread(db: Session, thread_id: str) -> Job | None:
    return db.scalars(select(Job).where(Job.thread_id == thread_id)).first()


def update_job(
    db: Session,
    job: Job,
    *,
    status: str | None = None,
    result: str | None = None,
    error_code: str | None = None,
    started: bool = False,
    finished: bool = False,
) -> Job:
    if status is not None:
        if status not in JOB_STATUSES:
            raise ValueError(f"invalid job status: {status}")
        job.status = status
    if result is not None:
        job.result = result
    if error_code is not None:
        job.error_code = error_code
    if started:
        job.started_at = _utcnow()
    if finished:
        job.finished_at = _utcnow()
    job.updated_at = _utcnow()
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def try_claim_job(db: Session, job: Job) -> bool:
    """Guarded transition queued -> running. Returns False if already handled."""
    if job.status not in ("queued",):
        return False
    job.status = "running"
    job.started_at = _utcnow()
    job.updated_at = _utcnow()
    db.add(job)
    db.commit()
    db.refresh(job)
    return True


# --- Memories ---


def create_memory(db: Session, *, user_id: str, content: str, category: str = "general") -> Memory:
    mem = Memory(id=str(uuid.uuid4()), user_id=user_id, content=content, category=category)
    db.add(mem)
    db.commit()
    db.refresh(mem)
    return mem


def list_memories(db: Session, *, user_id: str, category: str | None = None) -> list[Memory]:
    stmt = select(Memory).where(Memory.user_id == user_id).order_by(Memory.created_at.desc())
    if category:
        stmt = stmt.where(Memory.category == category)
    return list(db.scalars(stmt).all())


def get_memory(db: Session, memory_id: str) -> Memory | None:
    return db.get(Memory, memory_id)


def delete_memory(db: Session, memory: Memory) -> None:
    db.delete(memory)
    db.commit()


# --- Approvals ---


def create_approval(
    db: Session,
    *,
    user_id: str,
    job_id: str,
    thread_id: str,
    action: str,
    payload: dict[str, Any],
) -> Approval:
    ap = Approval(
        id=str(uuid.uuid4()),
        user_id=user_id,
        job_id=job_id,
        thread_id=thread_id,
        action=action,
        payload=json.dumps(payload or {}),
        status="pending",
    )
    db.add(ap)
    db.commit()
    db.refresh(ap)
    return ap


def get_approval(db: Session, approval_id: str) -> Approval | None:
    return db.get(Approval, approval_id)


def resolve_approval(db: Session, approval: Approval, *, status: str) -> Approval:
    if status not in ("approved", "rejected"):
        raise ValueError(f"invalid approval resolution: {status}")
    if approval.status != "pending":
        raise ValueError("approval already resolved")
    approval.status = status
    approval.resolved_at = _utcnow()
    db.add(approval)
    db.commit()
    db.refresh(approval)
    return approval


def get_pending_approval_for_thread(db: Session, thread_id: str) -> Approval | None:
    stmt = (
        select(Approval)
        .where(Approval.thread_id == thread_id, Approval.status == "pending")
        .order_by(Approval.created_at.desc())
    )
    return db.scalars(stmt).first()


def list_jobs_for_user(db: Session, *, user_id: str, limit: int = 50) -> list[Job]:
    stmt = select(Job).where(Job.user_id == user_id).order_by(Job.created_at.desc()).limit(limit)
    return list(db.scalars(stmt).all())


def list_approvals_for_user(
    db: Session, *, user_id: str, status: str | None = None, limit: int = 50
) -> list[Approval]:
    stmt = (
        select(Approval)
        .where(Approval.user_id == user_id)
        .order_by(Approval.created_at.desc())
        .limit(limit)
    )
    if status:
        stmt = (
            select(Approval)
            .where(Approval.user_id == user_id, Approval.status == status)
            .order_by(Approval.created_at.desc())
            .limit(limit)
        )
    return list(db.scalars(stmt).all())


def list_jobs_by_status(db: Session, *, status: str, limit: int = 100) -> list[Job]:
    stmt = select(Job).where(Job.status == status).order_by(Job.updated_at.asc()).limit(limit)
    return list(db.scalars(stmt).all())


def count_jobs_by_status(db: Session) -> dict[str, int]:
    from sqlalchemy import func

    rows = db.execute(select(Job.status, func.count(Job.id)).group_by(Job.status)).all()
    return {status: count for status, count in rows}


def list_active_user_ids(db: Session, *, limit: int = 500) -> list[str]:
    """Distinct user ids with jobs or memories (beat fan-out targets)."""
    job_ids = db.scalars(select(Job.user_id).distinct().limit(limit)).all()
    mem_ids = db.scalars(select(Memory.user_id).distinct().limit(limit)).all()
    seen: list[str] = []
    for uid in (*job_ids, *mem_ids):
        if uid and uid not in seen:
            seen.append(uid)
    return seen[:limit]


def list_stale_jobs(db: Session, *, stale_minutes: int = 30, limit: int = 100) -> list[Job]:
    """Jobs stuck in running/queued with no recent update (worker may have died)."""
    from datetime import timedelta

    cutoff = _utcnow().replace(tzinfo=None) - timedelta(minutes=stale_minutes)
    # Compare against naive UTC since SQLite stores naive datetimes.
    stmt = (
        select(Job)
        .where(
            Job.status.in_(["queued", "running"]),
        )
        .order_by(Job.updated_at.asc())
        .limit(limit * 2)
    )
    candidates = list(db.scalars(stmt).all())

    def _naive(dt) -> Any:
        if dt is None:
            return None
        try:
            return dt.replace(tzinfo=None)
        except Exception:
            return dt

    stale = [j for j in candidates if _naive(j.updated_at) and _naive(j.updated_at) < cutoff]
    return stale[:limit]


# --- Audit ---


def create_audit_event(
    db: Session,
    *,
    event_type: str,
    user_id: str | None = None,
    job_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    evt = AuditEvent(
        id=str(uuid.uuid4()),
        user_id=user_id,
        job_id=job_id,
        event_type=event_type,
        details=json.dumps(details or {}),
    )
    db.add(evt)
    db.commit()
    db.refresh(evt)
    return evt


def list_audit_events(
    db: Session, *, job_id: str | None = None, limit: int = 100
) -> list[AuditEvent]:
    stmt = select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)
    if job_id:
        stmt = (
            select(AuditEvent)
            .where(AuditEvent.job_id == job_id)
            .order_by(AuditEvent.created_at.desc())
            .limit(limit)
        )
    return list(db.scalars(stmt).all())


# --- User schedules (per-user proactive delivery) ---


def get_schedule(db: Session, user_id: str) -> "UserSchedule":
    """Return the user's schedule, creating defaults on first access."""
    from pa.db.models import UserSchedule

    row = db.get(UserSchedule, user_id)
    if row is None:
        row = UserSchedule(user_id=user_id)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def update_schedule(db: Session, user_id: str, **fields) -> "UserSchedule":
    row = get_schedule(db, user_id)
    for key, value in fields.items():
        if value is not None and hasattr(row, key):
            setattr(row, key, value)
    row.updated_at = _utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_schedules(db: Session) -> list:
    from pa.db.models import UserSchedule

    return list(db.scalars(select(UserSchedule)).all())
