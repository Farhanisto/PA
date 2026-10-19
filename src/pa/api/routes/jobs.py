"""GET /api/v1/jobs — durable job status/result with ownership check."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from pa.api.dependencies import get_current_user, get_db
from pa.db import repositories
from pa.schemas.assistant import ArtifactPageResponse, JobResponse

router = APIRouter()


@router.get(
    "/jobs",
    response_model=list[JobResponse],
    summary="List my jobs",
    description="Newest first, scoped to the caller. Used by dashboards for recent-job views.",
)
def list_jobs(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """List the caller's recent jobs, newest first."""
    jobs = repositories.list_jobs_for_user(db, user_id=user_id, limit=limit)
    return [
        JobResponse(
            job_id=j.id,
            thread_id=j.thread_id,
            status=j.status,
            request=j.request,
            result=j.result,
            error_code=j.error_code,
            created_at=j.created_at,
            started_at=j.started_at,
            finished_at=j.finished_at,
        )
        for j in jobs
    ]


@router.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
    summary="Get job status and result",
    description=(
        "Reads the durable job record (`queued` → `running` → "
        "`completed`/`failed`, or `waiting_for_approval`). "
        "Only the owning user may read a job."
    ),
)
def get_job_status(
    job_id: str, db: Session = Depends(get_db), user_id: str = Depends(get_current_user)
):
    """Poll a previously submitted job for its status/result."""
    job = repositories.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    if job.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not authorized")
    return JobResponse(
        job_id=job.id,
        thread_id=job.thread_id,
        status=job.status,
        request=job.request,
        result=job.result,
        error_code=job.error_code,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


@router.get(
    "/jobs/{job_id}/artifacts",
    summary="Structured app records from a run",
    description=(
        "The app records a job surfaced — HubSpot engagements, contacts, "
        "deals, triaged emails, ... — as cards the chat can render and the "
        "user can act on.\n\n"
        "Pass `limit` to get a page: `{items, total, offset, limit, "
        "has_more}`. Without it, the bare list is returned, so an older "
        "client keeps working against a newer API."
    ),
    responses={
        200: {
            "content": {
                "application/json": {
                    "schema": {
                        "oneOf": [
                            {"type": "array", "items": {"type": "object"}},
                            {"type": "object"},
                        ]
                    }
                }
            }
        }
    },
)
def job_artifacts(
    job_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
    offset: int = Query(default=0, ge=0),
    limit: int | None = Query(default=None, ge=1, le=100),
):
    """Return the cards produced by one of the caller's jobs."""
    job = repositories.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    if job.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not authorized")

    cards = repositories.job_artifacts(job)
    if limit is None:
        # The original contract: the whole list.
        return cards
    page = cards[offset : offset + limit]
    return ArtifactPageResponse(
        items=page,
        total=len(cards),
        offset=offset,
        limit=limit,
        has_more=offset + len(page) < len(cards),
    )
