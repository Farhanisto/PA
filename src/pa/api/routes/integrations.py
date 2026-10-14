"""Connected-app endpoints — status and read-only views.

Sends are never exposed here: outbound actions (email, WhatsApp, meetings)
only execute through approval-gated assistant workflows.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from pa.api.dependencies import get_current_user_id, get_db
from pa.integrations import manager
from pa.schemas.assistant import GmailMessage, IntegrationConnectResponse, IntegrationStatus

router = APIRouter()


@router.get(
    "/integrations",
    response_model=list[IntegrationStatus],
    summary="List connected apps",
    description="Providers with server-side configuration state (mock vs live). Never includes secrets.",
)
def list_integrations():
    """List Gmail/WhatsApp/Zoom connection state."""
    return [IntegrationStatus(**s) for s in manager.connected_accounts()]


@router.post(
    "/integrations/{provider}/connect",
    response_model=IntegrationConnectResponse,
    summary="Connect an app",
    description="Verifies server-side credentials exist for the provider and records an audit event. Idempotent.",
)
def connect_integration(
    provider: str, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
):
    """Connect Gmail, WhatsApp, or Zoom for the current user."""
    try:
        status_payload = manager.connect(db, user_id=user_id, provider=provider)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return IntegrationConnectResponse(
        provider=provider, connected=True, mode=status_payload.get("mode")
    )


@router.delete(
    "/integrations/{provider}/disconnect",
    response_model=IntegrationConnectResponse,
    summary="Disconnect an app",
    description="Records a disconnect audit event. Server-side credentials are managed by the operator.",
)
def disconnect_integration(
    provider: str, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)
):
    """Disconnect a provider for the current user."""
    try:
        manager.disconnect(db, user_id=user_id, provider=provider)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return IntegrationConnectResponse(provider=provider, connected=False)


@router.get(
    "/integrations/gmail/inbox",
    response_model=list[GmailMessage],
    summary="Read Gmail inbox",
    description="Read-only triage view of recent messages (mock data unless Gmail is configured).",
)
def gmail_inbox(limit: int = Query(default=10, ge=1, le=25)):
    """Read recent Gmail messages."""
    from pa.integrations.gmail import GmailConnector

    return [GmailMessage(**m) for m in GmailConnector().read_inbox(limit)]


@router.get(
    "/integrations/zoom/meetings",
    summary="List Zoom meetings",
    description="Read-only view of upcoming meetings (mock data unless Zoom is configured).",
)
def zoom_meetings():
    """List upcoming Zoom meetings."""
    from pa.integrations.zoom import ZoomConnector

    return ZoomConnector().list_meetings()
