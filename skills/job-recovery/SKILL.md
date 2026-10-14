---
name: job-recovery
description: Inspect stale PA jobs and safely requeue work after worker outages. Use when jobs are stuck in queued/running, after restarts, or when monitoring queue health.
---

# Job Recovery

`task_id` alone does not guarantee exactly-once execution — the database
job record plus guarded transitions do. The admin process lives in
`pa.recover`.

## Commands

```bash
python -m pa.recover --stale-minutes 30            # list stale jobs
python -m pa.recover --stale-minutes 30 --requeue --dry-run   # preview
python -m pa.recover --stale-minutes 30 --requeue  # re-dispatch
python -m pa.recover --queue-depth                 # DB counts + broker depth
```

## Rules

- Stale = `queued`/`running` with no update for `--stale-minutes`.
- Re-fetch each job before touching it; skip anything that left
  `queued`/`running` (completed/failed/cancelled/`waiting_for_approval`
  are never requeued).
- Reset stale `running` → `queued` (worker presumed dead), then re-dispatch.
- Confirm work is not already executing elsewhere before requeueing.
- Monitor Redis availability, worker health, provider errors, queue depth,
  and jobs stuck in `running`; `/ready` distinguishes liveness from
  dependency readiness.
