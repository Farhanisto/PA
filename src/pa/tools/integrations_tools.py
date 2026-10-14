"""Allowlisted integration tools — imported for side effect (registration)."""

from pa.integrations import gmail as _gmail_mod
from pa.integrations import whatsapp as _whatsapp_mod
from pa.integrations import zoom as _zoom_mod
from pa.tools.registry import register


@register("gmail_read_inbox")
def gmail_read_inbox(max_results: int = 10) -> str:
    import json

    try:
        limit = max(1, min(int(max_results), 25))
    except (TypeError, ValueError):
        raise ValueError("max_results must be an integer")
    messages = _gmail_mod.GmailConnector().read_inbox(limit)
    return json.dumps(messages)


@register("gmail_send")
def gmail_send(to: str, subject: str, body: str) -> str:
    import json

    out = _gmail_mod.GmailConnector().send_email(to=to, subject=subject, body=body)
    return json.dumps(out)


@register("whatsapp_read_recent")
def whatsapp_read_recent(limit: int = 10) -> str:
    import json

    try:
        n = max(1, min(int(limit), 25))
    except (TypeError, ValueError):
        raise ValueError("limit must be an integer")
    return json.dumps(_whatsapp_mod.WhatsAppConnector().read_recent(n))


@register("whatsapp_send")
def whatsapp_send(to: str, body: str) -> str:
    import json

    out = _whatsapp_mod.WhatsAppConnector().send_message(to=to, body=body)
    return json.dumps(out)


@register("zoom_list_meetings")
def zoom_list_meetings() -> str:
    import json

    return json.dumps(_zoom_mod.ZoomConnector().list_meetings())


@register("zoom_create_meeting")
def zoom_create_meeting(topic: str, start_time: str = "", duration_minutes: int = 30) -> str:
    import json

    try:
        duration = int(duration_minutes)
    except (TypeError, ValueError):
        raise ValueError("duration_minutes must be an integer")
    out = _zoom_mod.ZoomConnector().create_meeting(
        topic=topic, start_time=start_time or "", duration_minutes=duration
    )
    return json.dumps(out)
