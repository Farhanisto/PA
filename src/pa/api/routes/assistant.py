"""POST /api/v1/assistant/tasks — accept prompt, enqueue background work."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from pa.api.dependencies import get_current_user, get_db
from pa.db import repositories
from pa.schemas.assistant import AssistantTaskRequest, AssistantTaskResponse, BriefingRequest
from pa.security import audit as audit_mod

router = APIRouter()


@router.post(
    "/assistant/tasks",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AssistantTaskResponse,
    summary="Submit an assistant task",
    description=(
        "Creates a durable job record and enqueues background execution. "
        "Returns HTTP 202 with `job_id`/`thread_id` immediately — poll "
        "`GET /api/v1/jobs/{job_id}` for the result."
    ),
    response_description="The accepted job (not the model result).",
)
def create_assistant_task(
    body: AssistantTaskRequest,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """Submit a prompt for background assistant execution."""
    job = repositories.create_job(db, user_id=user_id, request=body.request.strip())
    audit_mod.record(
        db,
        event_type="job.accepted",
        user_id=user_id,
        job_id=job.id,
        details={"thread_id": job.thread_id},
    )
    # Enqueue background execution with only JSON-serializable IDs.
    try:
        from pa.queue.tasks import run_assistant_job

        run_assistant_job.delay(job.id)
    except Exception:
        # Broker unavailable in local test env — job remains queued and can be
        # executed eagerly or requeued by recovery process.
        pass
    return AssistantTaskResponse(job_id=job.id, thread_id=job.thread_id, status=job.status)


@router.post(
    "/assistant/briefing",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AssistantTaskResponse,
    summary="Request a daily briefing",
    description=(
        "Creates a durable job record and enqueues the daily-briefing workflow "
        "for the current user. Poll `GET /api/v1/jobs/{job_id}` for the result."
    ),
    response_description="The accepted briefing job (not the briefing itself).",
)
def create_briefing_task(
    body: BriefingRequest,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """Request a daily briefing built from seeded tasks, notes, and preferences."""
    scheduled_for = (body.scheduled_for or "").strip()[:128]
    job = repositories.create_job(
        db, user_id=user_id, request=f"Daily briefing {scheduled_for}".strip()
    )
    audit_mod.record(
        db,
        event_type="job.accepted",
        user_id=user_id,
        job_id=job.id,
        details={"thread_id": job.thread_id, "kind": "briefing"},
    )
    try:
        from pa.queue.tasks import run_daily_briefing

        run_daily_briefing.delay(user_id, scheduled_for, job.id)
    except Exception:
        pass
    return AssistantTaskResponse(job_id=job.id, thread_id=job.thread_id, status=job.status)
