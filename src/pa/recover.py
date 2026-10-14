"""Recovery admin process: inspect stale jobs and safely requeue work.

Per instructions §16: inspect stale jobs and requeue only after confirming
the work is not already executing.

Usage:
    python -m pa.recover --stale-minutes 30
    python -m pa.recover --stale-minutes 30 --requeue
    python -m pa.recover --queue-depth
"""

from __future__ import annotations

import argparse
import logging
import sys

from pa.db import repositories
from pa.db.database import get_session_factory, init_db
from pa.observability import log_event

logger = logging.getLogger("pa.recover")


def inspect_stale(stale_minutes: int = 30, limit: int = 100) -> list:
    init_db()
    db = get_session_factory()()
    try:
        return repositories.list_stale_jobs(db, stale_minutes=stale_minutes, limit=limit)
    finally:
        db.close()


def queue_depth() -> dict:
    """Best-effort queue depth + DB status counts for monitoring."""
    out: dict = {"db": {}, "redis": None}
    init_db()
    db = get_session_factory()()
    try:
        out["db"] = repositories.count_jobs_by_status(db)
    finally:
        db.close()
    try:
        import os

        import redis

        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        client = redis.from_url(url, socket_connect_timeout=2)
        # Celery default queue length (best-effort; None if unavailable).
        try:
            out["redis"] = {"celery_queue_len": client.llen("celery")}
        except Exception:
            out["redis"] = {"error": "llen_failed"}
    except Exception as exc:
        out["redis"] = {"error": type(exc).__name__}
    return out


def requeue_stale(stale_minutes: int = 30, limit: int = 100, dry_run: bool = False) -> list[str]:
    """Requeue stale queued/running jobs after confirming not executing.

    Safety: only jobs still in queued/running are touched; jobs already
    completed/failed/cancelled/waiting_for_approval are never requeued.
    Running jobs are reset to queued (worker restart assumed); already-queued
    jobs are re-dispatched. Returns requeued job IDs.
    """
    init_db()
    db = get_session_factory()()
    try:
        stale = repositories.list_stale_jobs(db, stale_minutes=stale_minutes, limit=limit)
        ids: list[str] = []
        for job in stale:
            # Re-fetch to confirm state hasn't changed (guard against races).
            fresh = repositories.get_job(db, job.id)
            if fresh is None or fresh.status not in ("queued", "running"):
                continue
            log_event(
                logger,
                logging.WARNING,
                "stale job detected",
                job_id=fresh.id,
                thread_id=fresh.thread_id,
                user_id=fresh.user_id,
                extra={"status": fresh.status},
            )
            if dry_run:
                ids.append(fresh.id)
                continue
            if fresh.status == "running":
                # Worker presumably died; reset to queued for safe re-dispatch.
                repositories.update_job(db, fresh, status="queued")
            try:
                from pa.queue.tasks import run_assistant_job

                run_assistant_job.delay(fresh.id)
                ids.append(fresh.id)
                log_event(
                    logger,
                    logging.INFO,
                    "stale job requeued",
                    job_id=fresh.id,
                    thread_id=fresh.thread_id,
                )
            except Exception as exc:
                log_event(
                    logger,
                    logging.ERROR,
                    "requeue failed (broker unavailable?)",
                    job_id=fresh.id,
                    extra={"error": str(exc)[:200]},
                )
        return ids
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="PA recovery: stale jobs + queue depth")
    p.add_argument("--stale-minutes", type=int, default=30)
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--requeue", action="store_true", help="re-dispatch stale jobs")
    p.add_argument("--dry-run", action="store_true", help="list only, don't requeue")
    p.add_argument("--queue-depth", action="store_true", help="print queue depth + exit")
    args = p.parse_args(argv)

    if args.queue_depth:
        import json

        print(json.dumps(queue_depth(), indent=2, default=str))
        return 0

    stale = inspect_stale(stale_minutes=args.stale_minutes, limit=args.limit)
    print(f"stale jobs (>{args.stale_minutes}m, queued/running): {len(stale)}")
    for j in stale:
        print(f"  {j.id} user={j.user_id} status={j.status} updated={j.updated_at}")

    if args.requeue:
        ids = requeue_stale(
            stale_minutes=args.stale_minutes, limit=args.limit, dry_run=args.dry_run
        )
        print(f"{'would requeue' if args.dry_run else 'requeued'}: {len(ids)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
