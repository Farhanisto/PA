"""Celery application — background dispatch, execution, scheduling."""

import os

from celery import Celery

from pa.config import get_settings


def _broker_url() -> str:
    return os.getenv("REDIS_URL") or get_settings().REDIS_URL


def _backend_url() -> str | None:
    return os.getenv("CELERY_RESULT_BACKEND") or get_settings().CELERY_RESULT_BACKEND


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
)
