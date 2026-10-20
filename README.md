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
| 10 | App records as interactive cards | `pa/services/artifacts.py`; `/api/v1/jobs/{id}/artifacts` |

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

## How app records reach the chat

A run's tool payload is normalised into **cards** — one structured card per
record (a HubSpot engagement, a contact, a deal, ...) — and stored on the job.

1. `run_read_tool` executes the tool and normalises the raw payload via
   `pa/services/artifacts.py`. HubSpot has bespoke profiles; every other app
   falls back to a generic shape, so a newly connected app works unchanged.
2. `GET /api/v1/jobs/{id}/artifacts` returns the cards for a run.
3. The chat renders them and the user acts on them in place: **Reply** drafts
   a body via `POST /api/v1/email/drafts/compose` and creates a draft for
   review; **Create task** / **Log note** focus the record and pre-fill the
   composer for the assistant to carry out.

Drafting deliberately bypasses the agent loop, so "draft a reply" can never
become a tool call that sends something. Only sending is approval-gated.

### The inbox briefing is cards too

A briefing does not come from a Composio tool — it reads **triaged messages
from the database** — so it builds its own cards (`build_email_cards`).
Those carry what a raw Gmail payload does not: the triage priority, whether
a reply is needed, and the deadlines that were found.

Only the messages worth acting on get a card (needing a reply, high
priority, or carrying a deadline). The newsletter tail is left out on
purpose — the prose already says it is safely ignorable, and 39 low-priority
cards would bury the three that matter. Normal mail is summarised on
demand, as the prose says.

**A briefing covers every connected app**, not just the mailbox. Each app
contributes a couple of read tools whose records become cards
(`pa/services/briefing.py`). The tools are discovered by pattern as well as
by name, so an app whose slugs were renamed still contributes.

**Cards are paged.** `GET /api/v1/jobs/{id}/artifacts?offset=&limit=`
returns `{items, total, offset, limit, has_more}`, and the chat loads the
next page on demand. A briefing across several apps can surface dozens of
records; the first screen shows a dozen.

The endpoint **keeps its original contract**: without `limit` it returns the
bare list. The API and the web are deployed separately, so a response shape
must not change under a running frontend — and the frontend tolerates both
shapes anyway.

## Working *with* a record, not just reading it

Clicking a card **focuses** it. The focus is sent with the next message
(`jobs.focus`), and the assistant receives the record — its id, title and
current values — in both the prose prompt and the tool-selection prompt. So
"make it urgent" is answered with a tool call against *that* ticket, not a
guess at which ticket was meant.

The flow for a write is:

1. Click the card, then an action. The composer is pre-filled with a
   template that already names the record and its id, and the user completes
   it ("The note should say: customer wants dark mode").
2. The assistant picks the write tool and fills in the arguments, using the
   focused record's id.
3. Writes are always gated. The approval names the record it would change
   and shows the exact arguments that will run.
4. Only on approval does the worker execute it.

The focus is context, not a straitjacket: it says "if the request is about
this record, use this id", so an unrelated question still goes to the app it
names. The user can clear it at any time.

## Skills

`skills/<name>/SKILL.md` holds the domain knowledge for one kind of work:
which tools an app exposes, the order to use them in, and the rules that
keep the assistant from doing damage. They are loaded at runtime
(`pa/services/skills.py`) and injected into the prompts:

- the **Rules** section goes into the prose prompt (what must never happen);
- the rest goes into the tool-selection prompt (which tool, in what order).

A skill is selected by the app the request is about (`apps:` in the
frontmatter) or by a trigger word in the request (`triggers:`). A dedicated
skill beats a generic one, so a HubSpot request gets `hubspot-crm` and not
`crm-generic`.

Skills matter most because the tool list is size-bounded: a skill names the
important slugs (`HUBSPOT_RETRIEVE_PIPELINE_STAGES`) that may not fit in the
truncated list, which is what stops the model guessing a slug.

**A skill's slugs are checked against the live Composio catalog in the test
suite** (`tests/integration/test_skill_slugs.py`). Skills are written by
hand, and Composio renames tools between SDK versions — several HubSpot
slugs had drifted and would have taught the model to call tools that do not
exist. The test is skipped when the catalog is not reachable.

**Tool arguments drift the same way.** `GMAIL_FETCH_EMAILS` takes
`query` and `max_results`, not the older `maxResults` and `q`, and a stale
argument fails the whole call. Three defences:

- `fit_args()` drops the arguments a tool no longer accepts, so a rename
  costs one field rather than the feature;
- the insights and enrichments fall back to the tool's defaults if the
  fitted call still fails;
- `tests/integration/test_insight_args.py` checks the dashboard recipes
  against the live tool schemas.

Tool results are also read as **raw payloads** rather than the summarised
text: a large response (Gmail returns whole message bodies) truncated
mid-JSON stops parsing, which looks exactly like an empty inbox.

## Checking a deployment

`GET /api/v1/diagnostics` reports what a running build actually has: the
database's Alembic revision, whether the schema is behind the code, and
which skills were loaded. It exists because a deployment can be running an
image that predates a feature, or a database that was never migrated, and
neither shows up as an error — just as missing behaviour.

The app applies migrations on startup and then **verifies the schema
matches the code** (`verify_schema`), so a stale database fails loudly at
boot instead of later with an obscure `UndefinedColumn` error.

### Deploying

```bash
docker compose up -d --build --force-recreate api worker beat
```

Three things bite here, and all three fail *quietly*:

- **The build needs PyPI.** The default Docker bridge network denies it, so
  `pip install` fails and the **old image keeps running** — the app looks
  fine and simply lacks the new feature. The compose file sets
  `network: host` on the build for this reason.
- **One image for the api, worker and beat.** They used to build
  separately, so a rebuild of one left the others on day-old code. They now
  share `pa:local`, built once by the api.
- **Use `--no-cache` when a migration changes.** A cached `pip install`
  layer can otherwise carry an older set of migration files, and the
  container then fails with `Can't locate revision identified by '...'`.
  That error now says so plainly instead of dumping a stack trace.

Verify afterwards:

```bash
curl -s localhost:8000/api/v1/diagnostics | jq
```

## Model request budget

A provider's token budget is **per minute and shared across requests**, not
per request: one oversized prompt can starve the next one. Groq's on-demand
tier allows 8k tokens per minute, and HubSpot alone exposes 290 tools.

Three things keep a turn inside that budget:

- **Every prompt is size-bounded** (`MAX_PROMPT_CHARS` in
  `pa/graph/nodes.py`). The tool list is ranked by relevance and cut to fit;
  ranking matches on the tool *slug* (which carries the entity and the verb)
  far more heavily than on the description, and handles the plural/singular
  mismatch between "tickets" and `TICKET`.
- **The prose is only written when it is read.** A tool's summary replaces
  the assistant's prose, so the graph calls the model for prose *after*
  choosing a tool, and skips it entirely on the read path. A tool-using turn
  costs two model calls, not three.
- **The tool decision gets a small completion budget** — it is one JSON
  object, and completions count against the same limit.

A turn costs roughly 4k tokens end to end, so the on-demand tier sustains
about one turn a minute. For concurrent users, raise the provider's tier:
the 413 from Groq says so itself ("Upgrade to Dev Tier").

Model failures keep their status code (`pa/models/errors.py`), which is
what makes a token-budget or rate-limit failure retryable, and gives the
user a sentence they can act on instead of `model_error: APIStatusError`.
Retries for those wait for the window to refill rather than backing off in
seconds.

## The assistant only ever uses connected apps

This is enforced in code, not in the prompt, because a prompt is not a
boundary: asked about "tickets" with no context, a model will name a famous
ticketing app it was never connected to.

- **Tools** — `tool_index` only contains tools for the caller's connected
  apps, and `plan_action` narrows that further to the one app the request is
  about. A tool from another app is rejected even when it is callable.
- **Prose** — every answer is checked against the real Composio catalog.
  If it describes an app the user has not connected, it is corrected and
  re-asked; if it strays again, the answer is withheld rather than ship
  invented data. Mentioning an app *to ask the user to connect it* is
  allowed — that is the helpful answer.
- **Request** — naming an app that is not connected gets a deterministic
  "connect it first" reply, and no tool runs at all.

The app a request is about is detected from the request itself, falling back
to the conversation — so a bare answer like "tickets" to a clarifying
question still resolves to the app being discussed. Turns are grouped into a
conversation (`jobs.conversation_id`) to make that possible.

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