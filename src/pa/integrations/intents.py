"""Detect integration actions in free-text requests.

Maps e.g. "send an email to bob@example.com about X" to a validated
(tool, args) pair for the approval proposal. Heuristic and conservative:
returns None when no clear integration intent is found, and the generic
approval path is used instead.
"""

import re
from typing import Any

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{6,}\d")


def detect_integration_action(request: str, summary: str = "") -> dict[str, Any] | None:
    text = (request or "").strip()
    lowered = text.lower()
    if not text:
        return None

    if "whatsapp" in lowered and any(k in lowered for k in ("send", "message", "text", "tell")):
        phone = _first_phone(text)
        body = _strip_trigger(text, ("whatsapp", "message", "text"))
        if phone:
            body = re.sub(re.escape(phone), "", body).strip(" .,;:-")
            body = re.sub(r"^to\s+", "", body, flags=re.IGNORECASE)
        if phone and body:
            return {"tool": "whatsapp_send", "args": {"to": phone, "body": body}}

    if "gmail" in lowered or "email" in lowered:
        if any(k in lowered for k in ("send", "write", "compose", "mail")):
            email = _first_email(text)
            subject, body = _split_subject_body(text)
            if email and (subject or body):
                return {
                    "tool": "gmail_send",
                    "args": {
                        "to": email,
                        "subject": subject or "(no subject)",
                        "body": body or subject,
                    },
                }

    if "zoom" in lowered or "meeting" in lowered:
        if any(k in lowered for k in ("schedule", "create", "set up", "setup", "book", "plan")):
            topic = _strip_trigger(text, ("zoom", "meeting"))
            return {
                "tool": "zoom_create_meeting",
                "args": {"topic": topic or "Meeting", "start_time": "", "duration_minutes": 30},
            }

    return None


def _first_email(text: str) -> str:
    m = EMAIL_RE.search(text)
    return m.group(0) if m else ""


def _first_phone(text: str) -> str:
    m = PHONE_RE.search(text)
    return m.group(0).strip() if m else ""


def _strip_trigger(text: str, triggers: tuple[str, ...]) -> str:
    out = text
    for t in triggers:
        out = re.sub(re.escape(t), "", out, flags=re.IGNORECASE)
    out = re.sub(r"\s+", " ", out).strip(" .,;:-")
    # Drop leading action verbs left over after trigger removal
    # ("Schedule a Zoom meeting for X" -> "X", not "Schedule a for X").
    out = re.sub(
        r"^(please\s+)?(schedule|create|set\s+up|setup|book|plan|send|write|compose)(\s+a)?\s+(for\s+)?",
        "",
        out,
        flags=re.IGNORECASE,
    )
    return out.strip(" .,;:-")[:500]


def _split_subject_body(text: str) -> tuple[str, str]:
    m = re.search(r"subject\s*[:\-]\s*(.+?)(?:\.|\n|$)", text, flags=re.IGNORECASE)
    if m:
        subject = m.group(1).strip()
        body = (text[: m.start()] + text[m.end() :]).strip(" .,;\n")
        return subject[:200], body[:2000]
    m = re.search(r"about\s+(.+)", text, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()[:200], text.strip()[:2000]
    return "", text.strip()[:2000]
