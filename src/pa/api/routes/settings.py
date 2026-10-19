"""User settings: proactive delivery schedule and other preferences."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from pa.api.dependencies import get_current_user, get_db
from pa.db import repositories
from pa.schemas.auth import ScheduleResponse, ScheduleUpdate

router = APIRouter(prefix="/settings", tags=["settings"])


def _now_in(tz_name: str) -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        return datetime.now(UTC)


@router.get("/schedule", response_model=ScheduleResponse, summary="My update schedule")
def get_schedule(user_id: str = Depends(get_current_user), db: Session = Depends(get_db)):
    """When the caller wants inbox briefings and follow-up reminders."""
    return ScheduleResponse.from_row(repositories.get_schedule(db, user_id))


@router.put("/schedule", response_model=ScheduleResponse, summary="Set my update schedule")
def put_schedule(
    body: ScheduleUpdate,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Choose the times proactive updates are delivered."""
    fields = body.model_dump(exclude_none=True)
    if "timezone" in fields:
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(fields["timezone"])
        except Exception:
            from fastapi import HTTPException

            raise HTTPException(status_code=400, detail=f"Unknown timezone: {fields['timezone']}")
    return ScheduleResponse.from_row(repositories.update_schedule(db, user_id, **fields))


@router.get("/timezone", response_model=dict, summary="Detect my timezone")
def detect_timezone():
    """Server-side offset, used to prefill the schedule form."""
    now = datetime.now(UTC)
    return {
        "utc_offset_minutes": int(now.utcoffset().total_seconds() // 60),
        "timezone": "UTC",
    }
