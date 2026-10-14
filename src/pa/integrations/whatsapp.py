"""WhatsApp connector via Twilio's WhatsApp API (httpx, already a dependency).

Live mode needs TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_WHATSAPP_FROM.
Mock mode serves seeded chats and records sends in-memory (no side effects).
"""

from typing import Any

import httpx

from pa.config import get_settings
from pa.integrations.base import public_status

PROVIDER = "whatsapp"

MOCK_CHATS = [
    {"from": "+15550001111", "body": "Don't forget milk on your way home", "date": "2026-10-13"},
    {"from": "+15550002222", "body": "Meeting moved to 3pm, see you there!", "date": "2026-10-12"},
]

# In-memory mock outbox: entries are {to, body}. Tests may clear it.
MOCK_OUTBOX: list[dict[str, str]] = []


def _creds() -> tuple[str, str, str]:
    s = get_settings()
    return s.TWILIO_ACCOUNT_SID, s.TWILIO_AUTH_TOKEN, s.TWILIO_WHATSAPP_FROM


class WhatsAppConnector:
    provider = PROVIDER

    def is_configured(self) -> bool:
        sid, token, sender = _creds()
        return bool(sid and token and sender)

    def mode(self) -> str:
        return "live" if self.is_configured() else "mock"

    def status(self) -> dict[str, Any]:
        return public_status(
            PROVIDER, self.is_configured(), "Send WhatsApp messages (approval-gated)."
        )

    def read_recent(self, limit: int = 10) -> list[dict[str, str]]:
        limit = max(1, min(int(limit), 25))
        # Twilio has no WhatsApp inbox-list API suitable here; recent chats
        # are served from local seeds in both modes (documented limitation).
        return [dict(m) for m in MOCK_CHATS[:limit]]

    def send_message(self, *, to: str, body: str) -> dict[str, str]:
        to = (to or "").strip()
        body = (body or "").strip()
        if not to:
            raise ValueError("recipient phone number required")
        if not body:
            raise ValueError("message body must not be empty")
        if len(body) > 1600:
            raise ValueError("message too long (max 1600 chars)")
        if not self.is_configured():
            MOCK_OUTBOX.append({"to": to, "body": body})
            return {"sid": f"mock-{len(MOCK_OUTBOX)}", "mode": "mock"}
        sid, token, sender = _creds()
        resp = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            auth=(sid, token),
            data={"From": sender, "To": to, "Body": body},
            timeout=30.0,
        )
        resp.raise_for_status()
        return {"sid": str(resp.json().get("sid", "")), "mode": "live"}
