"""Celery application — background dispatch, execution, scheduling."""

import os

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_ready

from pa.config import get_settings


def _broker_url() -> str:
    return os.getenv("REDIS_URL") or get_settings().REDIS_URL


def _backend_url() -> str | None:
    return os.getenv("CELERY_RESULT_BACKEND") or get_settings().CELERY_RESULT_BACKEND


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


celery_app = Celery(
    "pa",
    broker=_broker_url(),
    backend=_backend_url(),
    include=["pa.queue.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=300,
    task_soft_time_limit=270,
    result_expires=3600,
    timezone="Africa/Nairobi",
    enable_utc=True,
    beat_schedule={
        # Proactive email assistant: inbox briefings + follow-up reminders.
        "inbox-briefings": {
            "task": "pa.run_inbox_briefings",
            "schedule": crontab(
                hour=_env_int("INBOX_BRIEFING_HOUR", 8), minute=_env_int("INBOX_BRIEFING_MINUTE", 0)
            ),
        },
        "followup-reminders": {
            "task": "pa.run_followup_reminders",
            "schedule": crontab(
                hour=_env_int("FOLLOWUP_HOUR", 16), minute=_env_int("FOLLOWUP_MINUTE", 0)
            ),
        },
    },
)


@worker_ready.connect
def _worker_init(**kwargs) -> None:
    """Prepare durable stores before consuming tasks (best-effort)."""
    try:
        from pa.db.database import init_db

        init_db()
        from pa.graph.checkpointer import ensure_schema

        ensure_schema()
    except Exception:
        pass
