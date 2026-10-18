---
name: composio-connect
description: Connect user apps (Gmail, WhatsApp, Zoom) via Composio OAuth. Use when the user wants to link an app account or check link status.
---

# Composio Connect

Composio owns OAuth and per-user connected accounts; PA keeps execution on
its local connectors behind the approval gate. Implemented in
`pa.integrations.composio_client` (SDK imported lazily; needs
`COMPOSIO_API_KEY`).

## Flow

1. Ask who the link belongs to. Individuals link as themselves; businesses
   give an org ID, and the link is namespaced `org:{org}:user:{user}` so one
   person's OAuth never leaks into another org's jobs.
2. Start OAuth: `POST /api/v1/integrations/{provider}/composio-connect`
   (optional `?org_id=...`, or the Integrations UI tab guided flow) returns
   a `redirect_url`. The user completes it in a browser; record the
   `integration.composio_initiated` audit event.
3. Poll state: `GET /api/v1/integrations/{provider}/composio-status`
   (same `org_id`) → `active` | `pending` | `none` | `unavailable`. The UI
   "I've completed OAuth — check now" button polls up to a minute.
4. Revoke: `DELETE /api/v1/integrations/{provider}/composio` (same `org_id`)
   revokes the active connection and audits it.

## Rules

- Toolkit slugs mirror provider names (`gmail`, `whatsapp`, `zoom`); if
  auth-config discovery fails, create the config in the Composio dashboard
  or set `COMPOSIO_<PROVIDER>_AUTH_CONFIG_ID`.
- Optional `COMPOSIO_CALLBACK_URL` lets Composio notify on completion;
  without it, poll status.
- Never log or return API keys, tokens, or redirect internals beyond the
  URL the user must open. Status payloads carry state only.
- Execution still goes through approval-gated assistant workflows — a
  Composio link alone never authorizes a side effect.
