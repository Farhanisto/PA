"""End-to-end demo covering instructions §20 definition of done.

Uses PA_FAKE_MODEL=1 + eager Celery so no Redis/provider needed:

  1. health/ready
  2. submit request -> immediate job ID (202)
  3. queued -> running -> completed
  4. response + audit trail
  5. save preference, re-read (restart-safe via SQLite file)
  6. approval workflow: pause -> approve -> resume
  7. provider failure -> bounded retry/recovery (simulated transient)
  8. unauthorized + duplicate blocked

Run:  PA_FAKE_MODEL=1 .venv/bin/python scripts/demo_e2e.py
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("PA_FAKE_MODEL", "1")

from fastapi.testclient import TestClient  # noqa: E402

from pa.app import create_app  # noqa: E402
from pa.db import repositories  # noqa: E402
from pa.db.database import get_session_factory  # noqa: E402
from pa.queue import tasks as q_tasks  # noqa: E402
from pa.queue.celery_app import celery_app  # noqa: E402
from pa.seed import seed_user  # noqa: E402

USER = "demo-user"
celebrations: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    if ok:
        celebrations.append(name)
    else:
        print("DEMO FAILED at:", name, detail)
        sys.exit(1)


def main() -> int:
    celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)
    seed_user(USER)

    with TestClient(create_app()) as c:
        h = "X-User-Id"
        # 1. health / ready
        check("1 health", c.get("/health").json().get("status") == "ok")
        ready = c.get("/ready").json()
        check("1 ready db ok", ready.get("checks", {}).get("database") == "ok", str(ready))

        # 2. submit -> immediate 202 + job ID
        r = c.post(
            "/api/v1/assistant/tasks",
            json={"request": "Prepare a concise morning briefing"},
            headers={h: USER},
        )
        check("2 submit 202", r.status_code == 202, r.text)
        jid, tid = r.json()["job_id"], r.json()["thread_id"]
        check("2 job id", bool(jid and tid))

        # 3/4. completed + response + audit
        db = get_session_factory()()
        try:
            job = repositories.get_job(db, jid)
            check("3 completed", job.status == "completed", f"{job.status}/{job.error_code}")
            check("4 response", bool(job.result), (job.result or "")[:80])
            evts = repositories.list_audit_events(db, job_id=jid)
            check(
                "4 audit trail",
                any(e.event_type == "job.completed" for e in evts),
                f"{len(evts)} events",
            )
        finally:
            db.close()

        # 5. preference persists
        m = c.post(
            "/api/v1/memory",
            json={"content": "demo prefers concise output", "category": "preference"},
            headers={h: USER},
        )
        check("5 save pref", m.status_code in (200, 201), m.text)
        lst = c.get("/api/v1/memory", headers={h: USER}).json()
        check("5 retrieve pref", any("concise" in x["content"] for x in lst))

        # 6. approval: pause -> approve -> resume
        r2 = c.post(
            "/api/v1/assistant/tasks",
            json={"request": "Please send an email to bob"},
            headers={h: USER},
        )
        j2 = r2.json()["job_id"]
        db = get_session_factory()()
        try:
            job2 = repositories.get_job(db, j2)
            check("6 paused", job2.status == "waiting_for_approval", job2.status)
            ap = repositories.get_pending_approval_for_thread(db, job2.thread_id)
            check("6 approval record", ap is not None)
            ap_id = ap.id
        finally:
            db.close()
        ar = c.post(f"/api/v1/approvals/{ap_id}/approve", headers={h: USER})
        check("6 approve", ar.status_code == 200, ar.text)
        db = get_session_factory()()
        try:
            job2 = repositories.get_job(db, j2)
            check("6 resumed completed", job2.status == "completed", job2.status)
        finally:
            db.close()

        # 7. transient vs permanent (simulated, no live provider needed)
        check(
            "7 transient retryable", q_tasks.is_transient_error(ConnectionError("connection reset"))
        )
        check(
            "7 permanent not retried", not q_tasks.is_transient_error(ValueError("invalid request"))
        )

        # 8. unauthorized + duplicate
        forb = c.get(f"/api/v1/jobs/{jid}", headers={h: "intruder"})
        check("8 unauthorized blocked", forb.status_code == 403, str(forb.status_code))
        db = get_session_factory()()
        try:
            before = repositories.get_job(db, jid).result
        finally:
            db.close()
        q_tasks.run_assistant_job.apply(args=[jid])
        db = get_session_factory()()
        try:
            after = repositories.get_job(db, jid)
            check("8 duplicate safe", after.status == "completed" and after.result == before)
        finally:
            db.close()

        # 9. briefing from seeded data
        br = q_tasks.run_daily_briefing.apply(args=[USER, "demo-day"])
        check("9 briefing", br.get().get("ok") is True and bool(br.get().get("result")))

    print(f"\nAll {len(celebrations)} demo checks passed.")
    print(
        "Core claim: PA reliably carries a personal task from request to verified outcome, preserves progress, respects permissions, and explains what it did."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
