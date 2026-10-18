# PA

**Multi-user AI assistant for all your apps.** PA connects to the tools you
already use through Composio OAuth, so your app passwords are never stored by
PA. Email is the first fully implemented workflow: PA summarises and prioritises
your inbox, drafts replies for you to review and edit, and never sends anything
without your explicit approval. New apps join the same catalog and connection
model without a redeploy.

Stack: **FastAPI + LangGraph + Celery + Redis + Postgres + Next.js + Groq/Nebius + pytest**.

## Requirements coverage

| # | Requirement | Where |
|---|---|---|
| 1 | Authentication | `pa/auth/` — scrypt hashing, opaque revocable sessions; `/api/v1/auth/*` |
| 2 | App connections via Composio | `pa/integrations/` (Composio only); public `GET /api/v1/apps` |
| 3 | Summarize / prioritize / deadlines | `pa/services/email_triage.py`, `email_service.py` |
| 4 | Reply drafting | `email_service.generate_draft`, `/api/v1/email/drafts/generate` |
| 5 | Approval-gated sending | `send-request` → `/approvals/{id}/approve` → `resume_approved_workflow` |
| 6 | Proactive briefings + reminders | `pa/services/proactive.py` + celery beat |
| 7 | Observability | `pa/services/tracing.py`; `/api/v1/observability/*` |
| 8 | AI evaluations | `pa/services/evals.py`; `/api/v1/evals/*` |
| 9 | Security & audit | per-user scoping everywhere, deny-by-default permissions, audit trail |

## Run it

```bash
cp .env.example .env      # add GROQ_API_KEY and COMPOSIO_API_KEY
docker compose up -d --build
```

Open **http://localhost:3000**. You can browse the app catalog without an
account; sign in only when you want to connect an app or manage your inbox.

Services: `api` :8000 (Swagger at `/docs`), `web` :3000, `worker`, `beat`,
`postgres`, `redis`. Schema is managed by Alembic (`init_db()` runs
`alembic upgrade head` on startup).

## How an email gets sent

1. `POST /api/v1/email/sync` fetches and triages the mailbox.
2. `POST /api/v1/email/drafts/generate` drafts a reply; the user edits it.
3. `POST /api/v1/email/drafts/{id}/send-request` creates an approval record.
4. `POST /api/v1/approvals/{id}/approve` — only now does the worker send.
   Ownership, the `email.send` permission, and the recipient are all
   re-checked immediately before sending, and the send is idempotent.

## No demo data, no offline mode

There is no seeded inbox, no in-memory outbox, and no hardcoded fallback app
list. If Composio is unconfigured or you have not connected an app, the server
says so (`409` on a sync/send attempt, an empty catalog) instead of inventing
messages. Tests inject a Composio double via `composio_client.set_client_factory`
rather than relying on a demo path in production code.

## Try it with curl

```bash
BASE=http://127.0.0.1:8000

# Public: what can I connect?
curl -s $BASE/api/v1/apps

TOKEN=$(curl -s -X POST $BASE/api/v1/auth/signup \
  -H 'Content-Type: application/json' \
  -d '{"email":"me@example.com","password":"Str0ngPass123"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
AUTH="Authorization: Bearer $TOKEN"

# Connect an app (returns the URL to authorise)
curl -s -X POST $BASE/api/v1/integrations/gmail/connect -H "$AUTH"
curl -s $BASE/api/v1/integrations/gmail/status -H "$AUTH"

# Triage, draft, approve
curl -s -X POST $BASE/api/v1/email/sync -H "$AUTH"
curl -s $BASE/api/v1/email/messages -H "$AUTH"
curl -s $BASE/api/v1/observability/costs -H "$AUTH"
curl -s $BASE/api/v1/evals/classification -H "$AUTH"
```

## Tests

```bash
pytest                                  # backend
cd web && npm run typecheck && npm run build
```

## Security notes

- Passwords use `hashlib.scrypt`; sessions are opaque tokens stored only as
  SHA-256 hashes, so a database leak cannot be replayed.
- Every user-owned table has an explicit `user_id` and every query filters on
  it — there is no cross-user read path.
- Permissions (`email.read`, `email.send`, `email.proactive`, `email.auto_send`)
  are deny-by-default; `email.auto_send` starts off.
- Email bodies are treated as untrusted data and never as instructions.
- Before exposing publicly: HTTPS, secure cookie flags, rate limiting,
  and secret management.