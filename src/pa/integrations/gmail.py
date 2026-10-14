"""Gmail connector: read inbox, send email.

Live mode uses stdlib IMAP (read) + SMTP (send) with an app password —
no extra dependencies. Mock mode serves seeded data and records sends
to an in-memory outbox (visible to tests, no side effects).
"""

import imaplib
import smtplib
from email.header import decode_header
from email.message import EmailMessage
from email.parser import BytesParser
from typing import Any

from pa.config import get_settings
from pa.integrations.base import public_status

PROVIDER = "gmail"

MOCK_INBOX = [
    {
        "from": "standup@team.example",
        "subject": "Standup moved to 10:00",
        "snippet": "Quick heads-up: standup moves to 10:00 Africa/Nairobi starting Monday.",
        "date": "2026-10-13",
    },
    {
        "from": "invoices@shop.example",
        "subject": "Your invoice is ready",
        "snippet": "Invoice #1042 for $49.00 is ready. Due in 14 days.",
        "date": "2026-10-12",
    },
    {
        "from": "mom@family.example",
        "subject": "Sunday lunch?",
        "snippet": "Are you coming for lunch on Sunday? Let me know!",
        "date": "2026-10-11",
    },
]

# In-memory mock outbox: entries are {to, subject, body}. Tests may clear it.
MOCK_OUTBOX: list[dict[str, str]] = []


def _creds() -> tuple[str, str]:
    s = get_settings()
    return s.GMAIL_ADDRESS, s.GMAIL_APP_PASSWORD


class GmailConnector:
    provider = PROVIDER

    def is_configured(self) -> bool:
        address, password = _creds()
        return bool(address and password)

    def mode(self) -> str:
        return "live" if self.is_configured() else "mock"

    def status(self) -> dict[str, Any]:
        return public_status(
            PROVIDER, self.is_configured(), "Read inbox triage and send email (approval-gated)."
        )

    def read_inbox(self, max_results: int = 10) -> list[dict[str, str]]:
        max_results = max(1, min(int(max_results), 25))
        if not self.is_configured():
            return [dict(m) for m in MOCK_INBOX[:max_results]]
        address, password = _creds()
        conn = imaplib.IMAP4_SSL("imap.gmail.com", timeout=20)
        try:
            conn.login(address, password)
            conn.select("INBOX", readonly=True)
            _, data = conn.search(None, "ALL")
            ids = data[0].split()[-max_results:][::-1]
            messages = []
            for mid in ids:
                _, msg_data = conn.fetch(mid, "(RFC822.HEADER RFC822.TEXT)")
                raw = msg_data[0][1] if msg_data and msg_data[0] else b""
                parsed = BytesParser().parsebytes(raw)
                subject = _decode(parsed.get("Subject", ""))
                sender = _decode(parsed.get("From", ""))
                messages.append(
                    {
                        "from": sender[:200],
                        "subject": subject[:200],
                        "snippet": _snippet(parsed)[:300],
                        "date": parsed.get("Date", "")[:100],
                    }
                )
            return messages
        finally:
            try:
                conn.close()
            except Exception:
                pass
            try:
                conn.logout()
            except Exception:
                pass

    def send_email(self, *, to: str, subject: str, body: str) -> dict[str, str]:
        to = (to or "").strip()
        subject = (subject or "").strip()
        body = (body or "").strip()
        if not to or "@" not in to:
            raise ValueError("valid recipient email required")
        if not subject:
            raise ValueError("subject must not be empty")
        if not body:
            raise ValueError("body must not be empty")
        if len(body) > 10000:
            raise ValueError("body too long")
        if not self.is_configured():
            MOCK_OUTBOX.append({"to": to, "subject": subject, "body": body})
            return {"id": f"mock-{len(MOCK_OUTBOX)}", "mode": "mock"}
        address, password = _creds()
        msg = EmailMessage()
        msg["From"] = address
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(address, password)
            smtp.send_message(msg)
        return {"id": "sent", "mode": "live"}


def _decode(value: str) -> str:
    try:
        parts = decode_header(value or "")
        return "".join(
            (t.decode(enc or "utf-8", errors="replace") if isinstance(t, bytes) else t)
            for t, enc in parts
        )
    except Exception:
        return str(value or "")


def _snippet(parsed) -> str:
    try:
        if parsed.is_multipart():
            for part in parsed.walk():
                if part.get_content_type() == "text/plain" and not part.get_filename():
                    return part.get_payload(decode=True).decode(errors="replace").strip()[:300]
            return ""
        payload = parsed.get_payload(decode=True)
        if isinstance(payload, bytes):
            return payload.decode(errors="replace").strip()[:300]
        return str(payload or "").strip()[:300]
    except Exception:
        return ""
