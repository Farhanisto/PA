"""Integration account management.

Local-demo posture (matches project auth): credentials live in server-side
environment variables, never in the database. "Connecting" an app records an
audit event after verifying the provider is configured; per-user secret
vaulting is future work — do not expose this API publicly.
"""

from typing import Any

from sqlalchemy.orm import Session

from pa.db import repositories
from pa.integrations import gmail, whatsapp, zoom

PROVIDERS = ("gmail", "whatsapp", "zoom")


def get_connector(provider: str):
    if provider == gmail.PROVIDER:
        return gmail.GmailConnector()
    if provider == whatsapp.PROVIDER:
        return whatsapp.WhatsAppConnector()
    if provider == zoom.PROVIDER:
        return zoom.ZoomConnector()
    raise ValueError(f"unknown provider: {provider}")


def connected_accounts() -> list[dict[str, Any]]:
    return [get_connector(p).status() for p in PROVIDERS]


def connect(db: Session, *, user_id: str, provider: str) -> dict[str, Any]:
    connector = get_connector(provider)
    if not connector.is_configured():
        raise ValueError(
            f"{provider} is not configured on the server "
            "(set its credentials in the environment; mock mode needs no setup)"
        )
    repositories.create_audit_event(
        db, event_type="integration.connected", user_id=user_id, details={"provider": provider}
    )
    return connector.status()


def disconnect(db: Session, *, user_id: str, provider: str) -> dict[str, Any]:
    get_connector(provider)  # validates name
    repositories.create_audit_event(
        db, event_type="integration.disconnected", user_id=user_id, details={"provider": provider}
    )
    return {"provider": provider, "connected": False}
