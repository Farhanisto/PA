# PA

Personal AI assistant that remembers user-approved preferences, runs workflows in the background, and executes actions safely with human approval.

Stack: **FastAPI + LangGraph + Celery + Redis + Nemotron (Nebius Token Factory) + SQLite + Gradio + pytest**.

> **Auth note (MVP):** user identity is derived from `X-User-Id` header (default `demo-user`) for local single-user demo. Do not expose publicly without real authentication, HTTPS, rate limiting, and secret management. See `instructions.md` §15.

## Quickstart (local, no Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # then set NEBIUS_API_KEY + NEBIUS_MODEL
```

Run 3 terminals:

```bash
# Terminal 1 — API
uvicorn pa.app:app --reload --app-dir src

# Terminal 2 — worker (needs Redis)
docker compose up -d redis
celery -A pa.queue.celery_app:celery_app worker --loglevel=INFO

# Terminal 3 — tests
pytest
```

## Quickstart (Docker — API + worker + UI + Redis)

```bash
docker compose up -d --build
```

This starts `redis`, `api` (:8000), `worker`, and `ui` (:7860) with SQLite
persisted in the `pa_data` volume. Containers default to `PA_FAKE_MODEL=1`
(deterministic echo model); for real inference set `NEBIUS_API_KEY` and
`NEBIUS_MODEL` in your shell (or a local export — never commit them) and
restart:

```bash
NEBIUS_API_KEY=... NEBIUS_MODEL=... PA_FAKE_MODEL=0 docker compose up -d
```

Check status with `docker compose ps` and stop with `docker compose down`.
API logs: `docker compose logs api`. Frontend logs: `docker compose logs ui`.

Docs: `http://127.0.0.1:8000/docs` — Liveness: `GET /health`, Readiness: `GET /ready` (DB + Redis checks).

## API docs (Swagger)

FastAPI serves interactive docs automatically — no extra setup:

- Swagger UI: `http://127.0.0.1:8000/docs` (try every endpoint in the browser)
- ReDoc: `http://127.0.0.1:8000/redoc`
- Raw OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`

Every endpoint is tagged (`assistant`, `jobs`, `approvals`, `memory`, `ops`)
with summaries and response schemas. In Swagger UI, set the `X-User-Id`
header (default `demo-user`) via any endpoint's Headers section to act as
that user.

## Gradio frontend

Tabs for chat, jobs, approvals, memory, daily briefing, and health. Runs
in-process against the same SQLite DB, so no Redis needed for a local demo
(it falls back to eager execution when no worker is available):

```bash
PA_FAKE_MODEL=1 .venv/bin/python -m pa.frontend
# → http://127.0.0.1:7860
```

To serve the UI mounted inside the API at `/ui`:

```bash
PA_ENABLE_UI=1 uvicorn pa.app:app --app-dir src
# → http://127.0.0.1:8000/ui
```

## Demo (no Redis/provider needed)

```bash
.venv/bin/python -m pa.seed --user demo-user
PA_FAKE_MODEL=1 .venv/bin/python scripts/demo_e2e.py
```

Covers §20: submit→202, queued→completed, audit trail, memory persistence,
approval pause/resume, transient vs permanent errors, 403 + duplicate safety,
seeded briefing.

## Connected apps (Gmail, WhatsApp, Zoom)

Connect everyday apps so the assistant can do human-assistance jobs —
triage the inbox, send messages, schedule meetings. Status and read-only
views live under `GET /api/v1/integrations`; **sends only execute through
approval-gated workflows** (there is no direct send endpoint).

```bash
# See what's connected (mock vs live — never exposes secrets)
curl http://127.0.0.1:8000/api/v1/integrations

# Read-only views
curl "http://127.0.0.1:8000/api/v1/integrations/gmail/inbox?limit=5"
curl http://127.0.0.1:8000/api/v1/integrations/zoom/meetings
```

Without credentials every provider runs in **mock mode** (seeded inbox,
chats, meetings; sends recorded in-memory). For live mode set the matching
vars in `.env` (see `.env.example`): Gmail needs `GMAIL_ADDRESS` plus an
app password; WhatsApp needs Twilio `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN`
/ `TWILIO_WHATSAPP_FROM`; Zoom needs Server-to-Server OAuth
`ZOOM_ACCOUNT_ID` / `ZOOM_CLIENT_ID` / `ZOOM_CLIENT_SECRET`.

Example jobs (pause for approval, then execute on approve):

```bash
curl -X POST http://127.0.0.1:8000/api/v1/assistant/tasks \
  -H 'Content-Type: application/json' -H 'X-User-Id: demo-user' \
  -d '{"request":"Send an email to bob@example.com about lunch tomorrow"}'

curl -X POST http://127.0.0.1:8000/api/v1/assistant/tasks \
  -H 'Content-Type: application/json' -H 'X-User-Id: demo-user' \
  -d '{"request":"Schedule a Zoom meeting for sprint planning"}'
```

## Operations
```bash
# Seed demo tasks/notes/preferences (idempotent)
.venv/bin/python -m pa.seed --user demo-user

# Inspect stale jobs (queued/running with no recent update)
.venv/bin/python -m pa.recover --stale-minutes 30

# Safely requeue stale jobs (dry-run first)
.venv/bin/python -m pa.recover --stale-minutes 30 --requeue --dry-run
.venv/bin/python -m pa.recover --stale-minutes 30 --requeue

# Queue depth + DB status counts (monitoring)
.venv/bin/python -m pa.recover --queue-depth
```

Structured logs carry `correlation_id/job_id/thread_id` (`X-Correlation-Id`
header echoed); secrets are redacted. See `instructions.md` for full MVP spec,
architecture, and definition of done.

## Agent skills

`skills/` holds one `SKILL.md` per agent workflow (frontmatter `name` +
`description` for discovery, then operating instructions):

- `assistant-request` — submit a task, poll it to completion
- `daily-briefing` — briefing from seeded tasks/notes/preferences
- `approval-gated-action` — propose, pause, approve/reject, resume
- `persistent-memory` — scoped save/list/delete of preferences
- `job-recovery` — stale-job inspection and safe requeue

Validated by `tests/unit/test_skills.py`.

Submit a task (returns `202` + `job_id` immediately):

```bash
curl -X POST http://127.0.0.1:8000/api/v1/assistant/tasks \
  -H 'Content-Type: application/json' \
  -H 'X-User-Id: demo-user' \
  -d '{"request":"Prepare a concise morning briefing"}'
```

Check status:

```bash
curl http://127.0.0.1:8000/api/v1/jobs/<job_id> -H 'X-User-Id: demo-user'
```

For deterministic local runs without live inference:

```bash
PA_FAKE_MODEL=1 pytest
```

See `instructions.md` for full MVP spec, architecture, and definition of done.
