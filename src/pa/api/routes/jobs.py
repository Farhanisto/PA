"""GET /api/v1/jobs/{job_id} — durable job status/result with ownership check."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from pa.api.dependencies import get_current_user_id, get_db
from pa.db import repositories
from pa.schemas.assistant import JobResponse

router = APIRouter()


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
    job_id: str, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
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
    )
