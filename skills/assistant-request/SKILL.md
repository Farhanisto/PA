---
name: assistant-request
description: Submit a PA assistant task and track it to completion. Use when the user asks the assistant to do something (answer, reason, draft) that runs as a background LangGraph job.
---

# Assistant Request

Handles the general assistant workflow: a user prompt becomes a durable job,
a Celery worker runs the LangGraph graph (`understand_request →
prepare_context → generate_response → verify_result → finalize`), and the
result is persisted for retrieval.

## Submit a task

`POST /api/v1/assistant/tasks` with header `X-User-Id: <user>`:

```json
{ "request": "Prepare a concise morning briefing" }
```

Returns HTTP `202 Accepted` with `job_id`, `thread_id`, `status`. The `202`
means **accepted, not done** — never present it as a completed result.

## Track to completion

Poll `GET /api/v1/jobs/{job_id}` (same `X-User-Id`) until `status` is
terminal:

| Status | Meaning |
|---|---|
| `queued` / `running` | still working — keep polling |
| `completed` | `result` holds the response |
| `failed` | `error_code` explains why; do not retry blindly |
| `waiting_for_approval` | paused — hand off to the `approval-gated-action` skill |

## Rules

- Derive identity from the authenticated principal (`X-User-Id` in local
  demo). Never accept a caller-supplied `user_id` as proof of identity.
- Keep task payloads small and JSON-serializable (IDs + request data only).
- Treat the durable job record as authoritative, not the Celery result backend.
- Transient failures (timeouts, 502/503/504, rate limits) are retried with
  backoff; permanent errors (invalid request, auth) are marked failed.
- Never log API keys, auth headers, or unnecessary private content.
