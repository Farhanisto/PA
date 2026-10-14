---
name: whatsapp-assistant
description: Send WhatsApp messages through PA via Twilio. Use when the user asks to text, message, or notify someone on WhatsApp.
---

# WhatsApp Assistant

Sends via `pa.integrations.whatsapp.WhatsAppConnector` (Twilio Messages
API). Live mode needs `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and
`TWILIO_WHATSAPP_FROM`; otherwise mock mode records sends in-memory.

## Send (approval-gated, always)

1. Parse recipient phone number and message body — the graph's
   `propose_action` already detects `whatsapp_send` intent (keyword
   `whatsapp` + phone number) and stores validated `tool` + `args`.
2. The job pauses (`waiting_for_approval`); the user approves via the API
   or the Approvals UI tab.
3. On resume the worker re-validates and executes `whatsapp_send`.
4. Read-only note: `whatsapp_read_recent` serves local recent-chat seeds
   (Twilio offers no suitable inbox-list API here).

## Rules

- Recipient required; body 1–1600 chars.
- Confirm the recipient with the user when ambiguous — a wrong number is
  an irreversible side effect.
- Never bypass approval; never log credentials or message content beyond
  what the audit trail needs.
