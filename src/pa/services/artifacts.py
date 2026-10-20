"""Structured app data rendered as cards in the assistant.

The assistant's prose summary is lossy. A HubSpot engagement table arrives as
markdown text, so the UI cannot show a *record*, act on it, or let the user
respond to it. This module normalises a raw Composio tool payload into
*cards* — a small, stable shape the frontend renders and acts on.

Design rules:

- **Cards are data, never instructions.** Everything here comes from a
  third-party app and is passed through as display text only. No card field
  is ever interpreted as an instruction to the agent.
- **Never raises.** A malformed payload yields no cards, not a failed run.
- **Extensible.** HubSpot has bespoke profiles; every other app falls back to
  a generic shape, so a newly connected app works without code changes.
- **Bounded.** Card count and field lengths are capped so one huge tool
  result cannot blow up the job record or the chat.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# One chat turn should not render an unbounded number of cards.
MAX_CARDS = 25
MAX_BODY_CHARS = 1200
MAX_FIELD_CHARS = 300
MAX_TITLE_CHARS = 160
MAX_FIELDS = 8

# Engagement types HubSpot exposes, mapped to a human label.
ENGAGEMENT_LABELS = {
    "EMAIL": "Email",
    "CALL": "Call",
    "MEETING": "Meeting",
    "TASK": "Task",
    "NOTE": "Note",
    "CHAT": "Chat",
    "FORUM": "Forum",
}


# --- helpers ---------------------------------------------------------------


def _text(value: Any, limit: int = MAX_FIELD_CHARS) -> str:
    """Coerce anything to a trimmed single-line string."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return ", ".join(_text(v, limit) for v in value if v is not None)[:limit]
    if isinstance(value, dict):
        return json.dumps(value, default=str)[:limit]
    return str(value).strip()[:limit]


def _body(value: Any) -> str:
    """Body text keeps its line breaks, unlike other fields."""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = _text(value, MAX_BODY_CHARS * 2)
    return value.strip()[:MAX_BODY_CHARS]


def _records(payload: Any) -> list[dict[str, Any]]:
    """Pull a list of record dicts out of whatever shape the app returned.

    Apps wrap their results differently — ``{"results": [...]}``,
    ``{"data": {"results": [...]}}``, a bare list, ``{"items": [...]}`` — and
    the envelope changes between SDK versions. Rather than guess the keys,
    the payload is searched for the first list of record-like dicts, so a
    ticket list is still recognised when it arrives in an unfamiliar shape.
    """
    if payload is None:
        return []
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            return []
    return _find_records(payload)


# Keys that make a dict look like a record rather than a wrapper.
_RECORD_HINT_KEYS = (
    "id",
    "properties",
    "title",
    "name",
    "subject",
    "email",
    "type",
    "slug",
    "dealname",
    "firstname",
)

# Envelope keys tried before anything else.
_ENVELOPE_KEYS = ("results", "data", "items", "records", "objects", "messages", "entries")


def _looks_like_record(item: Any) -> bool:
    return isinstance(item, dict) and any(k in item for k in _RECORD_HINT_KEYS)


def _find_records(node: Any, depth: int = 0) -> list[dict[str, Any]]:
    """Depth-first search for the record list inside a payload."""
    if depth > 5:
        return []
    if isinstance(node, list):
        dicts = [x for x in node if isinstance(x, dict)]
        if node and len(dicts) == len(node):
            # A list of dicts: the records, if they look like records.
            if all(_looks_like_record(d) for d in dicts):
                return dicts
        for item in node[:5]:
            found = _find_records(item, depth + 1)
            if found:
                return found
        return dicts
    if isinstance(node, dict):
        for key in _ENVELOPE_KEYS:
            if key in node:
                found = _find_records(node[key], depth + 1)
                if found:
                    return found
        for key, value in node.items():
            if key in _RECORD_HINT_KEYS:
                continue
            found = _find_records(value, depth + 1)
            if found:
                return found
    return []


def _first(record: dict[str, Any], *paths: str) -> Any:
    """Read the first present key from a list of dotted paths."""
    for path in paths:
        cur: Any = record
        for part in path.split("."):
            if not isinstance(cur, dict):
                cur = None
                break
            cur = cur.get(part)
        if cur not in (None, "", [], {}):
            return cur
    return None


def _fmt_time(value: Any) -> str:
    """Render a timestamp as a short human string.

    Accepts HubSpot's millisecond epoch, second epoch, and ISO-8601.
    """
    if value in (None, ""):
        return ""
    dt: datetime | None = None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        raw = float(value)
        # Values below ~1e11 are second epochs; above are milliseconds.
        seconds = raw / 1000.0 if raw > 1e11 else raw
        try:
            dt = datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return ""
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        if text.replace(".", "", 1).isdigit():
            return _fmt_time(float(text))
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return ""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.strftime("%d %b %Y, %H:%M")


def _field(label: str, value: Any) -> dict[str, str] | None:
    text = _text(value)
    if not text:
        return None
    return {"label": label, "value": text[:MAX_FIELD_CHARS]}


def _fields(*pairs: tuple[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for label, value in pairs:
        f = _field(label, value)
        if f:
            out.append(f)
        if len(out) >= MAX_FIELDS:
            break
    return out


def build_email_cards(messages: list[Any]) -> list[dict[str, Any]]:
    """Cards for triaged email messages.

    The briefing reads messages from the database rather than from a tool,
    and it knows things a raw Gmail payload does not: the triage priority,
    whether a reply is needed, the deadlines it found. Those make a far
    better card than the generic one.

    ``messages`` are ``EmailMessage`` rows.
    """
    cards: list[dict[str, Any]] = []
    for message in messages:
        priority = _text(getattr(message, "priority", ""), 16).lower()
        needs_reply = bool(getattr(message, "needs_reply", False))
        subject = _text(getattr(message, "subject", ""), 200)
        from_addr = _text(getattr(message, "from_addr", ""), 320)
        summary = _text(getattr(message, "summary", ""), MAX_BODY_CHARS)
        snippet = _text(getattr(message, "snippet", ""), MAX_BODY_CHARS)
        received = _fmt_time(getattr(message, "received_at", None))
        thread_id = _text(getattr(message, "thread_id", ""), 128)
        message_id = _text(getattr(message, "id", ""), 64)
        deadlines = _json_list(getattr(message, "deadlines", "[]"))

        badges: list[dict[str, str]] = []
        for label, value in (("Priority", priority), ("Needs reply", "yes" if needs_reply else "")):
            badge = _badge(label, value)
            if badge:
                badges.append(badge)

        fields = _fields(
            ("From", from_addr),
            ("Priority", priority),
            ("Received", received),
            ("Deadline", deadlines[0] if deadlines else ""),
            ("Thread", thread_id),
        )

        actions: list[dict[str, Any]] = []
        recipient = _extract_address(from_addr)
        if recipient:
            actions.append(
                {
                    "id": "reply",
                    "label": "Draft reply",
                    "kind": "reply",
                    "recipient": recipient,
                    "subject": subject if subject.lower().startswith("re:") else f"Re: {subject}".strip(),
                }
            )
        if summary or snippet:
            actions.append(
                {
                    "id": "summarise",
                    "label": "Summarise",
                    "kind": "ask",
                    "prompt": f"Summarise the email '{subject}' from {from_addr}",
                }
            )
        actions.append(
            {
                "id": "task",
                "label": "Create task",
                "kind": "ask",
                "prompt": f"Create a task from the email '{subject}' from {from_addr}",
            }
        )

        if needs_reply:
            group = "Needs your reply"
        elif priority == "high":
            group = "High priority"
        elif deadlines:
            group = "Has a deadline"
        else:
            group = "Normal"

        cards.append(
            _card(
                card_id=f"email:{message_id or thread_id or subject}",
                app="email",
                kind="message",
                type_label="Email",
                title=subject or "(no subject)",
                subtitle=from_addr,
                body=summary or snippet,
                fields=fields,
                badges=badges,
                meta=received,
                actions=actions,
                group=group,
                record_id=thread_id or message_id,
            )
        )
    return cards


def _json_list(value: Any) -> list[str]:
    """A JSON list field as a list of strings."""
    if isinstance(value, list):
        return [str(v) for v in value if v]
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return []
    return [str(v) for v in parsed if v] if isinstance(parsed, list) else []


def _extract_address(addr: str) -> str:
    """The email address in a From header."""
    match = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", addr or "")
    return match.group(0) if match else ""


def _card(
    *,
    card_id: str,
    app: str,
    kind: str,
    type_label: str,
    title: str,
    subtitle: str = "",
    body: str = "",
    fields: list[dict[str, str]] | None = None,
    badges: list[dict[str, str]] | None = None,
    meta: str = "",
    actions: list[dict[str, Any]] | None = None,
    group: str = "",
    record_id: str = "",
) -> dict[str, Any]:
    """Assemble a card in the shape the frontend renders."""
    return {
        "id": card_id,
        "app": app,
        "kind": kind,
        "type": type_label,
        "title": (title or "Untitled").strip()[:MAX_TITLE_CHARS],
        "subtitle": subtitle.strip()[:MAX_TITLE_CHARS],
        "body": body,
        "fields": fields or [],
        "badges": badges or [],
        "meta": meta.strip()[:MAX_FIELD_CHARS],
        "actions": actions or [],
        "group": group.strip()[:80],
        # The id the app knows this record by, so the assistant can act on
        # exactly this one.
        "record_id": record_id.strip()[:128],
    }


# Severity words that should read as a warning or a danger.
_TONE_WORDS = {
    "high": "danger",
    "urgent": "danger",
    "critical": "danger",
    "error": "danger",
    "failed": "danger",
    "overdue": "danger",
    "medium": "warn",
    "normal": "warn",
    "pending": "warn",
    "open": "warn",
    "in progress": "warn",
    "low": "neutral",
    "closed": "neutral",
    "done": "neutral",
    "resolved": "neutral",
}


def _badge(label: str, value: Any) -> dict[str, str] | None:
    """A small coloured tag, e.g. Priority: HIGH."""
    text = _text(value, 40)
    if not text:
        return None
    return {
        "label": label,
        "value": text,
        "tone": _TONE_WORDS.get(text.lower(), "neutral"),
    }


# --- HubSpot ---------------------------------------------------------------


def _hubspot_props(record: dict[str, Any]) -> dict[str, Any]:
    props = record.get("properties")
    return props if isinstance(props, dict) else {}


def _prop(record: dict[str, Any], props: dict[str, Any], *keys: str) -> Any:
    """A HubSpot property, read from the properties bag or the record itself.

    HubSpot's API nests fields under ``properties``, but tools that flatten
    the record are common enough that both shapes must work.
    """
    for source in (props, record):
        for key in keys:
            value = source.get(key)
            if value not in (None, "", [], {}):
                return value
    return None


def _hubspot_engagement(record: dict[str, Any], app: str) -> dict[str, Any]:
    """An engagement (email, call, meeting, task, note) on a contact or deal."""
    meta = record.get("metadata")
    meta = meta if isinstance(meta, dict) else {}
    props = _hubspot_props(record)

    raw_type = _text(_first(record, "type", "metadata.type"), 32).upper()
    label = ENGAGEMENT_LABELS.get(raw_type, raw_type.title() or "Engagement")

    title = _text(
        _first(
            record,
            "metadata.title",
            "metadata.subject",
            "metadata.meetingTitle",
            "subject",
            "title",
            "name",
        ),
        MAX_TITLE_CHARS,
    )
    body = _body(
        _first(record, "metadata.body", "body", "content", "description", "notes")
    )

    eng_id = _text(_first(record, "id", "engagement.id"), 64)
    created = _first(record, "timestamp", "createdAt", "engagement.createdAt", "metadata.hs_timestamp")
    meta_text = _fmt_time(created)

    # Who it is about: the associated contact/deal ids are opaque, but the
    # metadata usually carries the human name or address.
    who = _text(
        _first(
            record,
            "metadata.toEmail",
            "metadata.fromEmail",
            "metadata.email.to",
            "metadata.email.from",
            "metadata.attendees",
            "metadata.to",
            "metadata.from",
        ),
        MAX_TITLE_CHARS,
    )

    details = _fields(
        ("Engagement ID", eng_id),
        ("Created", meta_text),
        ("Subject", _first(record, "metadata.subject", "subject")),
        ("From", _first(record, "metadata.fromEmail", "metadata.email.from")),
        ("To", _first(record, "metadata.toEmail", "metadata.email.to")),
        ("Attendees", _first(record, "metadata.attendees")),
        ("Starts", _fmt_time(_first(record, "metadata.startTime", "metadata.start"))),
        ("Ends", _fmt_time(_first(record, "metadata.endTime", "metadata.end"))),
        ("Location", _first(record, "metadata.location")),
    )

    actions: list[dict[str, Any]] = []
    recipient = _text(
        _first(record, "metadata.fromEmail", "metadata.email.from", "metadata.toEmail", "metadata.email.to"),
        320,
    )
    subject = _text(_first(record, "metadata.subject", "subject"), 200)

    if raw_type == "EMAIL" and recipient:
        actions.append(
            {
                "id": "reply",
                "label": "Reply",
                "kind": "reply",
                "recipient": recipient,
                "subject": subject or (title or "Your reply"),
            }
        )
    elif raw_type in ("EMAIL", "CALL", "MEETING"):
        actions.append(
            {
                "id": "draft",
                "label": "Draft reply",
                "kind": "reply",
                "recipient": recipient,
                "subject": subject or title,
            }
        )

    actions.append(
        {
            "id": "task",
            "label": "Create task",
            "kind": "ask",
            "prompt": (
                f"Create a HubSpot task for {who or 'this contact'}: {title or label}"
                f" — {body[:280]}. The task should say:"
            ),
        }
    )
    actions.append(
        {
            "id": "note",
            "label": "Log note",
            "kind": "ask",
            "prompt": (
                f"Log a note on the HubSpot record for {who or 'this contact'}: "
                f"The note should say:"
            ),
        }
    )

    return _card(
        card_id=f"{app}:engagement:{eng_id or title}",
        app=app,
        kind="engagement",
        type_label=label,
        title=title or label,
        subtitle=who,
        body=body,
        fields=details,
        meta=meta_text,
        actions=actions,
    )


def _hubspot_contact(record: dict[str, Any], app: str) -> dict[str, Any]:
    props = _hubspot_props(record)
    first = _text(_prop(record, props, "firstname", "first_name"), 80)
    last = _text(_prop(record, props, "lastname", "last_name"), 80)
    name = _text(_prop(record, props, "name"), 160) or " ".join(
        p for p in (first, last) if p
    )
    email = _text(_prop(record, props, "email"), 320)
    rec_id = _text(record.get("id"), 64)

    actions: list[dict[str, Any]] = []
    if email:
        actions.append(
            {
                "id": "email",
                "label": "Draft email",
                "kind": "reply",
                "recipient": email,
                "subject": "",
            }
        )
    actions.append(
        {
            "id": "deal",
            "label": "Create deal",
            "kind": "ask",
            "prompt": (
                f"Create a HubSpot deal for the contact {name or email} (ID {rec_id}). "
                f"The deal should be:"
            ),
        }
    )
    actions.append(
        {
            "id": "note",
            "label": "Log note",
            "kind": "ask",
            "prompt": (
                f"Log a note on the HubSpot contact {name or email} (ID {rec_id}). "
                f"The note should say:"
            ),
        }
    )

    return _card(
        card_id=f"{app}:contact:{rec_id or name}",
        app=app,
        kind="contact",
        type_label="Contact",
        title=name or email or "Contact",
        subtitle=email,
        body=_body(_prop(record, props, "notes", "description")),
        fields=_fields(
            ("Email", email),
            ("Phone", _prop(record, props, "phone")),
            ("Company", _prop(record, props, "company")),
            ("Lifecycle stage", _prop(record, props, "lifecyclestage")),
            ("Created", _fmt_time(_first(record, "createdate", "createdAt"))),
            ("Contact ID", rec_id),
        ),
        actions=actions,
        record_id=rec_id,
    )


def _hubspot_company(record: dict[str, Any], app: str) -> dict[str, Any]:
    props = _hubspot_props(record)
    name = _text(_prop(record, props, "name"), 160)
    rec_id = _text(record.get("id"), 64)
    return _card(
        card_id=f"{app}:company:{rec_id or name}",
        app=app,
        kind="company",
        type_label="Company",
        title=name or "Company",
        subtitle=_text(_prop(record, props, "domain"), 160),
        body=_body(_prop(record, props, "description")),
        fields=_fields(
            ("Domain", _prop(record, props, "domain")),
            ("Industry", _prop(record, props, "industry")),
            ("Employees", _prop(record, props, "numberofemployees")),
            ("Created", _fmt_time(_first(record, "createdate", "createdAt"))),
            ("Company ID", rec_id),
        ),
        record_id=rec_id,
        actions=[
            {
                "id": "note",
                "label": "Log note",
                "kind": "ask",
                "prompt": (
                    f"Log a note on the HubSpot company {name} (ID {rec_id}). "
                    f"The note should say:"
                ),
            }
        ],
    )


def _hubspot_deal(record: dict[str, Any], app: str) -> dict[str, Any]:
    props = _hubspot_props(record)
    name = _text(_prop(record, props, "dealname", "name"), 160)
    rec_id = _text(record.get("id"), 64)
    amount = _text(_prop(record, props, "amount"), 40)
    stage = _text(_prop(record, props, "dealstage"), 80)
    return _card(
        card_id=f"{app}:deal:{rec_id or name}",
        app=app,
        kind="deal",
        type_label="Deal",
        title=name or "Deal",
        subtitle=stage,
        body=_body(_prop(record, props, "description")),
        fields=_fields(
            ("Amount", amount),
            ("Stage", stage),
            ("Pipeline", _prop(record, props, "pipeline")),
            ("Close date", _fmt_time(_prop(record, props, "closedate"))),
            ("Created", _fmt_time(_first(record, "createdate", "createdAt"))),
            ("Deal ID", rec_id),
        ),
        badges=[b for b in (_badge("Stage", stage),) if b],
        record_id=rec_id,
        actions=[
            {
                "id": "stage",
                "label": "Update stage",
                "kind": "ask",
                "prompt": (
                    f"Update the HubSpot deal '{name}' (ID {rec_id}). "
                    f"I want to change it so that:"
                ),
            },
            {
                "id": "note",
                "label": "Log note",
                "kind": "ask",
                "prompt": (
                    f"Log a note on the HubSpot deal '{name}' (ID {rec_id}). "
                    f"The note should say:"
                ),
            },
        ],
    )


def _hubspot_ticket(record: dict[str, Any], app: str) -> dict[str, Any]:
    props = _hubspot_props(record)
    subject = _text(_prop(record, props, "subject", "name"), 160)
    rec_id = _text(record.get("id"), 64)
    created = _fmt_time(_first(record, "createdate", "createdAt", "properties.createdate"))
    modified = _fmt_time(
        _first(record, "hs_lastmodifieddate", "lastmodifieddate", "updatedAt")
    )
    stage = _text(_prop(record, props, "hs_pipeline_stage"), 80)
    pipeline = _text(_prop(record, props, "hs_pipeline"), 40)
    priority = _text(_prop(record, props, "hs_ticket_priority"), 40)
    category = _text(_prop(record, props, "hs_ticket_category"), 40)
    badges = [b for b in (_badge("Priority", priority), _badge("Stage", stage)) if b]
    return _card(
        card_id=f"{app}:ticket:{rec_id or subject}",
        app=app,
        kind="ticket",
        type_label="Ticket",
        title=subject or "Ticket",
        subtitle=category,
        body=_body(_prop(record, props, "content", "description")),
        fields=_fields(
            ("Status", stage),
            ("Priority", priority),
            ("Category", category),
            ("Pipeline", pipeline),
            ("Created", created),
            ("Updated", modified),
            ("Ticket ID", rec_id),
        ),
        badges=badges,
        meta=created,
        record_id=rec_id,
        actions=[
            {
                "id": "note",
                "label": "Log note",
                "kind": "ask",
                "prompt": (
                    f"Log a note on the HubSpot ticket '{subject}' (ID {rec_id}). "
                    f"The note should say:"
                ),
            },
            {
                "id": "update",
                "label": "Update ticket",
                "kind": "ask",
                "prompt": (
                    f"Update the HubSpot ticket '{subject}' (ID {rec_id}). "
                    f"Change it so that:"
                ),
            },
        ],
    )


# HubSpot tool slug fragment -> builder. Order matters: the first match wins,
# so the more specific families are listed before the generic ones.
_HUBSPOT_PROFILES: list[tuple[str, Any]] = [
    ("ENGAGEMENT", _hubspot_engagement),
    ("CONTACT", _hubspot_contact),
    ("COMPAN", _hubspot_company),
    ("DEAL", _hubspot_deal),
    ("TICKET", _hubspot_ticket),
]


def _hubspot_cards(tool_slug: str, records: list[dict[str, Any]], app: str) -> list[dict[str, Any]]:
    upper = tool_slug.upper()
    builder = None
    for fragment, fn in _HUBSPOT_PROFILES:
        if fragment in upper:
            builder = fn
            break
    if builder is None:
        return []
    return [builder(rec, app) for rec in records]


# --- generic fallback ------------------------------------------------------


def _generic_card(record: dict[str, Any], app: str, kind: str) -> dict[str, Any]:
    """A card for any app we have no bespoke profile for.

    Only common, well-known keys are read, so an unfamiliar payload still
    produces something readable rather than a wall of raw JSON.
    """
    rec_id = _text(_first(record, "id", "uuid", "recordId"), 64)
    title = _text(
        _first(record, "title", "name", "subject", "headline", "summary", "text"),
        MAX_TITLE_CHARS,
    )
    body = _body(_first(record, "body", "description", "content", "snippet", "preview"))
    subtitle = _text(
        _first(record, "from", "from_addr", "sender", "author", "user", "account"),
        MAX_TITLE_CHARS,
    )
    created = _fmt_time(_first(record, "created_at", "createdAt", "timestamp", "date", "sent_at"))

    fields = _fields(
        ("ID", rec_id),
        ("Created", created),
        ("Type", _first(record, "type", "kind", "category")),
        ("Status", _first(record, "status", "state")),
    )

    actions: list[dict[str, Any]] = []
    recipient = _text(_first(record, "email", "to", "to_addr", "recipient"), 320)
    if recipient:
        actions.append(
            {
                "id": "email",
                "label": "Draft email",
                "kind": "reply",
                "recipient": recipient,
                "subject": title,
            }
        )
    actions.append(
        {
            "id": "ask",
            "label": "Ask about this",
            "kind": "ask",
            "prompt": f"Tell me more about this {app} record: {title or rec_id}",
        }
    )

    return _card(
        card_id=f"{app}:{kind}:{rec_id or title}",
        app=app,
        kind=kind,
        type_label=kind.replace("_", " ").title(),
        title=title or rec_id or "Record",
        subtitle=subtitle,
        body=body,
        fields=fields,
        meta=created,
        actions=actions,
    )


# --- public API ------------------------------------------------------------


def build_cards(tool_slug: str, app: str, payload: Any) -> list[dict[str, Any]]:
    """Normalise a tool payload into cards. Never raises.

    ``payload`` is the raw Composio response (dict, list, or JSON string).
    Apps without a bespoke profile fall back to a generic card shape.
    """
    try:
        records = _records(payload)
        if not records:
            return []

        app_slug = (app or "").lower()
        cards: list[dict[str, Any]] = []

        if app_slug == "hubspot":
            cards = _hubspot_cards(tool_slug, records, app_slug)
        if not cards:
            kind = _generic_kind(tool_slug)
            cards = [_generic_card(rec, app_slug or "app", kind) for rec in records]

        # Drop anything that ended up with no usable content.
        cards = [c for c in cards if c["title"] or c["body"] or c["fields"]]
        return cards[:MAX_CARDS]
    except Exception as exc:  # never break a run over a rendering detail
        logger.warning("artifact build failed for %s: %s", tool_slug, type(exc).__name__)
        return []


def _generic_kind(tool_slug: str) -> str:
    """Guess an entity kind from the tool slug for the generic profile."""
    upper = tool_slug.upper()
    for fragment, kind in (
        ("ENGAGEMENT", "engagement"),
        ("CONTACT", "contact"),
        ("COMPAN", "company"),
        ("DEAL", "deal"),
        ("TICKET", "ticket"),
        ("LEAD", "lead"),
        ("MESSAGE", "message"),
        ("MAIL", "message"),
        ("EVENT", "event"),
        ("TASK", "task"),
        ("NOTE", "note"),
        ("ORDER", "order"),
        ("INVOICE", "invoice"),
    ):
        if fragment in upper:
            return kind
    parts = [p for p in upper.split("_") if p]
    return parts[-1].lower() if parts else "record"
