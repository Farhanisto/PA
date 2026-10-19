---
name: composio-connect
description: Connect or disconnect any app (Gmail, HubSpot, Slack, ...) via Composio OAuth, and inspect connection state. Use when the user wants to link an app account or check link status.
---

# Composio Connect

Composio owns OAuth and per-user connected accounts; PA never stores user
credentials. Implemented in `pa.integrations.composio_client` (SDK imported
lazily; needs `COMPOSIO_API_KEY`) and `pa.integrations.catalog`.

## Flow

1. Start OAuth: `POST /api/v1/integrations/{provider}/connect`
   (optional `?org_id=...`) returns a `redirect_url`. Individuals link as
   themselves; businesses namespace the ref `org:{org}:user:{user}` so one
   person's OAuth never leaks into another org's jobs. The route records an
   `integration.connect_started` audit event.
2. The user completes it in a browser and approves access.
3. Poll state: `GET /api/v1/integrations/{provider}/status` → `active` |
   `pending` | `none` | `unavailable`. The Apps panel's "I approved it" button
   uses this.
4. Revoke: `DELETE /api/v1/integrations/{provider}` revokes the active
   connection.
5. Inspect what the agent can call: `GET /api/v1/integrations/{provider}/tools`
   returns the toolkit's tools plus `connected`/`state`.

## Rules

- **Any toolkit slug from `GET /api/v1/apps` is accepted** (~1600 of them);
  slugs are normalised, not allowlisted. There is no fixed provider list.
- Each toolkit needs an **auth config** (a blueprint shared by all users)
  before a connection can be created. Discovery is automatic via
  `auth_configs.list`; to pin one explicitly set
  `COMPOSIO_<PROVIDER>_AUTH_CONFIG_ID` in `.env` (e.g.
  `COMPOSIO_GMAIL_AUTH_CONFIG_ID=ac_...`).
- The project API key needs **`connected_accounts: write`** to create links.
  A read-only key yields `403` and surfaces as a 502 with an explanatory
  message.
- `link.create()` is the supported start-flow; `initiate()` is deprecated for
  managed OAuth. SDK is pinned to `composio==0.28.0`.
- Never log or return API keys, tokens, or redirect internals beyond the URL
  the user must open. Status payloads carry state only.
- A Composio link alone never authorizes a side effect — execution still goes
  through approval-gated assistant workflows.