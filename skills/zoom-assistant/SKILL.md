---
name: zoom-assistant
description: List and schedule Zoom meetings through PA. Use when the user asks about upcoming meetings or scheduling a call.
---

# Zoom Assistant

Manages meetings via `pa.integrations.zoom.ZoomConnector`. Live mode uses
Zoom Server-to-Server OAuth (`ZOOM_ACCOUNT_ID`, `ZOOM_CLIENT_ID`,
`ZOOM_CLIENT_SECRET`, token cached in memory); otherwise mock mode serves
seeded meetings and mock join URLs.

## List (no approval needed)

- Tool `zoom_list_meetings()` and `GET /api/v1/integrations/zoom/meetings`
  return `{id, topic, start_time, join_url}` for upcoming meetings.

## Schedule (approval-gated, always)

1. Parse topic (required), optional start time and duration (1–480 min,
   default 30) — the graph's `propose_action` already detects
   `zoom_create_meeting` intent (`zoom`/`meeting` + schedule/create/book).
2. The job pauses; the user approves via the API or Approvals UI tab.
3. On resume the worker re-validates and executes `zoom_create_meeting`,
   returning the join URL in the job result.
4. Share the join URL from the completed job result.

## Rules

- Topic is required; never schedule without an explicit user request.
- Times are user-provided strings passed through to Zoom — confirm
  timezone-sensitive meetings before approving.
