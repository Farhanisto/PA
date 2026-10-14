# PrivateOS — Build Instructions

## 1. Project goal

Build **PrivateOS**, a personal AI assistant that can remember user-approved preferences, run useful workflows in the background, and execute actions safely with human approval when needed.

The MVP stack:

- **FastAPI** — HTTP API and client communication
- **LangGraph** — stateful assistant workflows, checkpointing, interrupts, and resumption
- **Celery** — background job execution and scheduled work
- **Redis** — Celery message broker
- **Nemotron via Nebius Token Factory** — remote reasoning model
- **SQLite** — initial local database and LangGraph checkpoint persistence
- **pytest** — tests

Keep the architecture modular so SQLite can later be replaced by PostgreSQL and a production-compatible LangGraph checkpointer.

## 2. MVP success criteria

The MVP is complete when all of the following work:

1. A user can submit an assistant task through FastAPI.
2. The API returns `202 Accepted` and a job ID without waiting for the model to finish.
3. Celery picks up the task and runs a LangGraph workflow.
4. LangGraph calls the configured Nemotron model through Nebius.
5. Task status and results can be retrieved through an API endpoint.
6. Graph state is checkpointed persistently and can be resumed.
7. The assistant can pause before a sensitive action and wait for explicit user approval.
8. User memory is stored separately from transient graph state.
9. Failures are logged, retried only when appropriate, and visible through job status.
10. Tests cover successful execution, failures, authorization, and approval/resumption.

### MVP workflows

Implement these in order:

1. **Assistant request** — accept a prompt, reason with Nemotron, and return a response.
2. **Daily briefing draft** — combine user-provided tasks and notes into a concise briefing. Start with mock or locally seeded data rather than broad third-party integrations.
3. **Approval-gated action** — create a proposed action, pause the graph, and resume only after an authorized approval.
4. **Persistent memory** — save, list, and delete user-approved preferences.

Do not start with voice, autonomous purchases, a multi-agent swarm, or unrestricted shell access.

## 3. Architecture and ownership boundaries

```text
Client (CLI or dashboard)
        |
        v
FastAPI
  |-- authenticate and validate requests
  |-- create/read durable job records
  |-- enqueue background tasks
  |-- expose approval and memory endpoints
        |
        +--------------------------+
        |                          |
        v                          v
Celery + Redis                Database
        |                     jobs, users, memory,
        v                     approvals, audit events
Celery worker
        |
        v
LangGraph
  |-- state and workflow routing
  |-- Nemotron model calls
  |-- approved tool execution
  |-- checkpoints and interrupts
        |
        v
Nebius Token Factory / tools
```

**Responsibilities:**

- FastAPI owns HTTP handling, authentication, input validation, and response schemas.
- Celery owns background dispatch, task execution, and scheduling.
- Redis is the queue broker; do not use it as the only durable record of jobs or user memory.
- LangGraph owns each workflow's state transitions, checkpointing, and approval interrupts.
- The model provides reasoning; it must not bypass tool policies.
- The database stores durable application records and user memory.
- Tool adapters enforce authorization and validate all arguments before external side effects.

Do not build a second custom state machine that duplicates LangGraph's graph transitions.

## 4. Repository structure

Create this structure:

```text
PA/
├── README.md
├── instructions.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── docker-compose.yml
├── src/
│   └── privateos/
│       ├── __init__.py
│       ├── app.py
│       ├── config.py
│       ├── api/
│       │   ├── __init__.py
│       │   ├── dependencies.py
│       │   └── routes/
│       │       ├── __init__.py
│       │       ├── assistant.py
│       │       ├── jobs.py
│       │       ├── approvals.py
│       │       └── memory.py
│       ├── schemas/
│       │   ├── __init__.py
│       │   ├── assistant.py
│       │   ├── jobs.py
│       │   ├── approvals.py
│       │   └── memory.py
│       ├── db/
│       │   ├── __init__.py
│       │   ├── database.py
│       │   ├── models.py
│       │   └── repositories.py
│       ├── graph/
│       │   ├── __init__.py
│       │   ├── state.py
│       │   ├── builder.py
│       │   ├── nodes.py
│       │   └── checkpointer.py
│       ├── models/
│       │   ├── __init__.py
│       │   └── nebius.py
│       ├── queue/
│       │   ├── __init__.py
│       │   ├── celery_app.py
│       │   └── tasks.py
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── registry.py
│       │   └── briefing.py
│       ├── memory/
│       │   ├── __init__.py
│       │   └── service.py
│       └── security/
│           ├── __init__.py
│           ├── policy.py
│           ├── approvals.py
│           └── audit.py
└── tests/
    ├── conftest.py
    ├── unit/
    ├── integration/
    └── security/
```

Keep each module focused. Avoid putting the whole implementation in `app.py` or `tasks.py`.

## 5. Environment and dependencies

Use Python 3.11 or newer, and pin tested dependency versions before deploying.

Example `pyproject.toml` dependencies:

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "privateos"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "fastapi",
  "uvicorn[standard]",
  "pydantic-settings",
  "python-dotenv",
  "httpx",
  "langgraph",
  "langchain-core",
  "langchain-openai",
  "celery[redis]",
  "redis",
  "sqlalchemy",
]

[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio", "httpx", "ruff", "mypy"]

[tool.setuptools.packages.find]
where = ["src"]
```

Add a `.env.example` file:

```dotenv
APP_ENV=development
APP_HOST=127.0.0.1
APP_PORT=8000

DATABASE_URL=sqlite:///./privateos.db

REDIS_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1

NEBIUS_API_KEY=
NEBIUS_BASE_URL=https://api.tokenfactory.nebius.com/v1/
NEBIUS_MODEL=
NEBIUS_TIMEOUT=60
NEBIUS_MAX_TOKENS=2048

LANGGRAPH_CHECKPOINT_DB=./privateos_checkpoints.db
```

Do not commit `.env`, real credentials, tokens, user data, or production database files. Use `.env.example` to document required configuration.

The exact Nebius model identifier must be selected from the currently available models in the user's Nebius account/catalog. Do not assume a placeholder model ID is valid. Keep the model ID configurable.

## 6. Local development infrastructure

Create `docker-compose.yml` with Redis for local development:

```yaml
services:
  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    ports:
      - "127.0.0.1:6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

volumes:
  redis_data:
```

Start it:

```bash
docker compose up -d redis
```

Install the application:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Set `NEBIUS_API_KEY` and `NEBIUS_MODEL` in `.env` before testing real inference.

## 7. Implement configuration first

In `config.py`, use Pydantic Settings to load environment variables and validate required values. Avoid reading secrets at import time in unrelated modules.

Requirements:

- Fail with a clear error if a required provider key is missing when remote inference is requested.
- Keep timeouts and model names configurable.
- Do not log API keys or authorization headers.
- Separate development and production configuration.
- Avoid global clients that open connections merely because a module was imported.

## 8. Implement the Nebius model adapter

Create `models/nebius.py`.

Preferred approach: use `langchain_openai.ChatOpenAI` if the selected Nebius endpoint supports its OpenAI-compatible chat completions API.

```python
import os
from langchain_openai import ChatOpenAI


def create_nebius_model() -> ChatOpenAI:
    api_key = os.getenv("NEBIUS_API_KEY")
    if not api_key:
        raise RuntimeError("NEBIUS_API_KEY is not configured")

    model_name = os.getenv("NEBIUS_MODEL")
    if not model_name:
        raise RuntimeError("NEBIUS_MODEL is not configured")

    return ChatOpenAI(
        model=model_name,
        base_url=os.getenv(
            "NEBIUS_BASE_URL",
            "https://api.tokenfactory.nebius.com/v1/",
        ),
        api_key=api_key,
        temperature=0.2,
        timeout=float(os.getenv("NEBIUS_TIMEOUT", "60")),
        max_retries=2,
        max_tokens=int(os.getenv("NEBIUS_MAX_TOKENS", "2048")),
    )
```

Verify the current provider documentation and test the exact endpoint and model ID. If the deployment uses a different API contract, implement a provider adapter for that contract rather than assuming the URL or response shape.

Do not instantiate a model globally in a way that requires a secret at module import. Prefer a dependency/factory and cache a client safely at application or worker scope.

Add tests with a fake model so most tests do not require live API calls.

## 9. Implement the database and durable job records

Start with SQLAlchemy and SQLite. Create records for at least:

- `Job`: `id`, `user_id`, `thread_id`, `status`, `request`, `result`, `error_code`, `created_at`, `updated_at`, `started_at`, `finished_at`.
- `Memory`: `id`, `user_id`, `content`, `category`, `created_at`, `updated_at`.
- `Approval`: `id`, `user_id`, `job_id`, `thread_id`, `action`, `payload`, `status`, `created_at`, `resolved_at`.
- `AuditEvent`: `id`, `user_id`, `job_id`, `event_type`, `details`, `created_at`.

Use a constrained set of job statuses, for example:

`queued`, `running`, `waiting_for_approval`, `completed`, `failed`, `cancelled`.

Create repository functions so route handlers and Celery tasks do not contain raw SQL. Add database migrations when the schema begins to evolve; Alembic is recommended.

Never trust a `user_id` supplied in a request body for authorization. Derive it from the authenticated principal. For an initial single-user local demo, make this limitation explicit and do not expose the API publicly.

## 10. Implement LangGraph state and workflow

Create `graph/state.py` with a typed state that contains only the information needed to execute a workflow. For example:

```python
from typing import TypedDict


class AssistantState(TypedDict, total=False):
    user_id: str
    request: str
    response: str
    task_items: list[str]
    proposed_action: dict
    approval_status: str
    error: str
```

Implement nodes in `graph/nodes.py`:

1. `understand_request`: classify the request and identify its intended workflow.
2. `prepare_context`: load only the user's authorized context.
3. `generate_response`: call Nemotron.
4. `verify_result`: check that the output is usable and meets the request.
5. `propose_action`: create a structured proposal for any sensitive side effect.
6. `finalize`: write the final response and workflow outcome.

Implement routing in `graph/builder.py` with `StateGraph`, conditional edges, and explicit termination paths. Keep the first graph simple; add subgraphs only when multiple workflows need them.

Requirements:

- Compile with a persistent checkpointer appropriate to the chosen database and deployment.
- Supply a stable `thread_id` in the LangGraph config.
- Do not use an in-memory checkpointer for workflows that must survive process restarts.
- Keep user memory separate from graph checkpoints.
- Validate all model-produced tool arguments.
- Set node-level timeouts or provider timeouts where supported.
- Bound retries and define how failures are represented in state.
- Never let the model directly execute arbitrary shell commands or Python code.

## 11. Implement human approval and resumption

Use LangGraph's interrupt/resume mechanism for approval-gated actions.

Example conceptual flow:

```text
prepare action proposal
        |
        v
persist approval record
        |
        v
LangGraph interrupt
        |
        v
worker records waiting_for_approval and exits
        |
        v
user approves or rejects via FastAPI
        |
        v
API enqueues a resume task
        |
        v
worker resumes same graph thread with persisted checkpointer
        |
        v
verify authorization and action outcome
```

Do not leave a Celery worker running while waiting for a human.

Implement these rules:

- The approval endpoint must verify that the authenticated user owns the approval and job.
- Approval must refer to the exact stored action proposal, not a replacement payload from the client.
- Reject duplicate or stale approval submissions.
- Persist the approval decision before enqueueing resume work.
- Resume using the same LangGraph `thread_id` and a supported checkpointer.
- Re-check policy and permissions immediately before executing the side effect.
- Use idempotency keys for side-effecting operations.
- If an action may have succeeded before a worker crash, check its outcome before retrying.

The exact Celery resume-task implementation must be tested against the installed LangGraph version and checkpointer. Do not assume an interrupt is resumed by simply calling `graph.invoke()` with the original input again.

## 12. Implement Celery and Redis

Create `queue/celery_app.py`:

```python
import os
from celery import Celery

celery_app = Celery(
    "privateos",
    broker=os.environ["REDIS_URL"],
    backend=os.getenv("CELERY_RESULT_BACKEND"),
    include=["privateos.queue.tasks"],
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
```

Keep Celery task payloads small and JSON-serializable. Pass identifiers and request data, not model objects, database sessions, open connections, or credentials.

Create tasks for:

- `run_assistant_job(job_id)`
- `resume_approved_workflow(approval_id)`
- `run_daily_briefing(user_id, scheduled_for)`

Each task should:

1. Load the durable job/approval record.
2. Check whether the work is already complete or already running.
3. Mark the job as running using a transaction or guarded state update.
4. Create or load the compiled LangGraph workflow.
5. Invoke or resume the graph with the stable thread ID.
6. Persist the final result, waiting-for-approval status, or safe error details.
7. Record audit events.
8. Re-raise only errors that should trigger Celery retry; mark permanent errors as failed.

Use Celery retries for transient infrastructure failures, such as a temporary provider outage. Do not retry invalid requests, authorization failures, or other permanent errors. Use exponential backoff and a maximum retry count for transient errors.

Important: `task_id` alone does not guarantee exactly-once execution. Use a database job record, idempotency checks, and guarded state transitions to prevent duplicate work.

## 13. Implement FastAPI routes

Create these versioned endpoints:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/assistant/tasks` | Create a job and enqueue it |
| `GET` | `/api/v1/jobs/{job_id}` | Return authorized job status/result |
| `POST` | `/api/v1/approvals/{approval_id}/approve` | Approve a stored proposal |
| `POST` | `/api/v1/approvals/{approval_id}/reject` | Reject a stored proposal |
| `GET` | `/api/v1/memory` | List the current user's memories |
| `POST` | `/api/v1/memory` | Save a user-approved memory |
| `DELETE` | `/api/v1/memory/{memory_id}` | Delete an owned memory |
| `GET` | `/health` | Basic process health check |

For `POST /assistant/tasks`:

1. Authenticate the caller.
2. Validate the request with Pydantic.
3. Create a durable job record and unique `thread_id`.
4. Enqueue `run_assistant_job` using the job ID.
5. Return HTTP `202 Accepted` with `job_id`, `thread_id`, and `status`.

Do not return a false success result just because the task was enqueued. The response means only that the job was accepted.

The job status endpoint must read the durable job record and verify ownership. Treat the Celery result backend as operational metadata, not the authoritative application database.

Create `app.py` to register routers and expose `/health`. Use FastAPI lifespan management for database resources and clean shutdown. Avoid creating event loops, network clients, or DB connections at module import time.

## 14. Persistent memory

Memory must be explicit, user-controlled, and separate from LangGraph workflow state.

MVP requirements:

- Save only user-approved facts/preferences.
- Scope every memory record to its owner.
- Allow the user to inspect and delete memories.
- Retrieve only memories needed for the current task.
- Record memory creation and deletion in the audit trail.
- Do not store credentials, access tokens, or unnecessary sensitive information.
- Treat content retrieved from email, documents, or web pages as untrusted data, not instructions.

Start with SQLite-backed retrieval and simple metadata/category filters. Add embeddings/vector search only after the basic behavior and deletion semantics are tested.

## 15. Security requirements

Before exposing PrivateOS outside localhost, implement:

- Authentication and per-user authorization.
- HTTPS termination and secure secret management.
- Rate limiting and request-size limits.
- Strict validation of all tool arguments and model outputs.
- Allowlisted tool registry; no arbitrary tool names from model output.
- Human approval for external side effects such as sending messages.
- Audit events for tool calls, approvals, denials, and failures.
- Timeouts and resource limits for model calls and tools.
- Sandboxing for untrusted code or filesystem execution; integrate OpenShell where appropriate.
- Redaction of secrets and sensitive content from logs.
- Redis and database access restricted to private networks.

Never assume a prompt instruction is a security boundary. Enforce policy in application code before every tool call and side effect.

## 16. Observability and recovery

Every request and task should have a `job_id`, `thread_id`, and correlation ID.

Log:

- Job accepted, started, completed, failed, or awaiting approval.
- Model/provider latency and retry count.
- Tool name, sanitized arguments, outcome, and duration.
- Checkpoint/resume errors.
- Approval decisions and policy denials.

Do not log API keys, full authorization headers, or unnecessary private user content.

Add structured logging and a health check. The health endpoint should distinguish process liveness from dependency readiness when deployed. Monitor Redis availability, worker health, provider errors, queue depth, and jobs stuck in `running`.

Implement a recovery command or administrative process to inspect stale jobs and safely requeue work after confirming it is not already executing.

## 17. Tests

Use fake model and tool implementations for deterministic tests.

### Unit tests

- Configuration fails clearly when required values are missing.
- Model adapter parses successful responses and handles provider errors.
- Graph routes requests to the expected nodes.
- Policy denies unauthorized tools and actions.
- Memory is scoped by user and can be deleted.
- Retry policy distinguishes transient and permanent failures.

### Integration tests

- Submit a task to FastAPI and verify a job is created.
- Run a Celery task against a test broker or a dedicated Redis instance.
- Verify the LangGraph result is persisted.
- Verify a task can resume from a checkpoint after recreating the graph object.
- Verify approval pauses execution and a later authorized approval resumes it.
- Verify a repeated task does not duplicate a side effect.

### Security tests

- User A cannot read User B's job, memory, or approval.
- A client cannot alter the action payload during approval.
- Invalid model-produced tool arguments are rejected.
- Secrets are absent from logs and API responses.
- Repeated or stale approvals cannot execute an action twice.

Keep live Nebius tests optional and gated by environment variables.

## 18. Run the application locally

Start Redis:

```bash
docker compose up -d redis
```

Install the project and set `.env`, then run three separate terminals.

Terminal 1 — API:

```bash
uvicorn privateos.app:app --reload --app-dir src
```

Terminal 2 — Celery worker:

```bash
celery -A privateos.queue.celery_app:celery_app worker --loglevel=INFO
```

Terminal 3 — tests:

```bash
pytest
```

Open the FastAPI interactive documentation at `http://127.0.0.1:8000/docs`.

Test a task submission:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/assistant/tasks \
  -H 'Content-Type: application/json' \
  -d '{"request":"Prepare a concise morning briefing"}'
```

This request body is illustrative. If authentication is enabled, include the required credentials and derive the user identity from them; do not accept a caller-selected `user_id` as proof of identity.

## 19. Implementation order

Build incrementally and keep the project runnable after each step:

1. **Scaffold:** package structure, configuration, linting, test setup, `.env.example`.
2. **Model adapter:** verify one real Nemotron response through the configured Nebius endpoint.
3. **Graph:** implement a minimal `StateGraph` with a fake-model test and then real inference.
4. **Persistence:** add durable job records and a persistent LangGraph checkpointer.
5. **Queue:** start Redis and Celery; execute the graph in a worker.
6. **API:** add task submission and status retrieval.
7. **Memory:** implement user-scoped create/list/delete operations.
8. **Approvals:** add interrupt, approval records, and reliable resumption.
9. **Briefing workflow:** use seeded tasks/notes and generate a useful briefing.
10. **Security:** enforce ownership, policy, audit, idempotency, and safe error handling.
11. **Reliability:** test worker restarts, provider timeouts, duplicate delivery, and recovery.
12. **Demo polish:** add documentation, seeded demo data, and an end-to-end demo script.

Do not move to the next stage until the previous stage has tests and a repeatable run command.

## 20. Definition of done

The MVP is ready to demonstrate when a developer can:

1. Start Redis, FastAPI, and a Celery worker.
2. Submit a request and receive a job ID immediately.
3. Observe the job move from queued to running to completed.
4. Inspect the response and audit trail.
5. Save a preference, restart the application, and retrieve it.
6. Trigger an approval-required workflow, approve it, and see it resume.
7. Simulate a provider failure and observe bounded retry/recovery.
8. Confirm that unauthorized access and duplicate side effects are blocked.

The core product claim should be: **PrivateOS reliably carries a personal task from request to verified outcome, preserves progress, respects permissions, and explains what it did.**
