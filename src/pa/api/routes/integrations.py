"""Connected-app endpoints.

Two shapes:
- ``GET /api/v1/apps`` — public catalog of connectable apps (no auth needed,
  so the UI can show what is available before sign-in).
- ``GET /api/v1/integrations`` — the caller's connection state (auth required).

Sending is never exposed here: outbound actions only run through
approval-gated workflows.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from pa.api.dependencies import get_current_user, get_db, get_optional_user
from composio_client import (
    AuthenticationError,
    ComposioError,
    PermissionDeniedError,
    RateLimitError,
)

from pa.db import repositories
from pa.integrations import catalog as catalog_mod
from pa.integrations import composio_client
from pa.schemas.auth import (
    AppResponse,
    ConnectionResponse,
    ToolInfo,
    ToolkitToolsResponse,
)

router = APIRouter(tags=["integrations"])


def _composio_http_error(exc: Exception) -> HTTPException:
    """Turn a Composio SDK failure into a message the user can act on.

    The SDK raises its own ComposioError hierarchy (permission denied,
    authentication, rate limit...), which is neither ValueError nor
    RuntimeError; without this it surfaced as an opaque 500.
    """
    name = type(exc).__name__
    detail = str(exc).strip() or name
    if isinstance(exc, PermissionDeniedError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "The Composio API key lacks permission for this action "
                f"({detail}). Widen the key's scopes in the Composio dashboard."
            ),
        )
    if isinstance(exc, AuthenticationError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Composio rejected the API key: {detail}",
        )
    if isinstance(exc, RateLimitError):
        return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=detail)
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Composio error: {detail}")


@router.get(
    "/apps",
    response_model=list[AppResponse],
    summary="Browse connectable apps",
    description="Public. Lists the apps available to connect via Composio, with this user's connection state when signed in.",
)
def list_apps(
    user_id: str | None = Depends(get_optional_user),
    refresh: bool = Query(default=False, description="Ignored; catalog is fetched live."),
):
    apps = catalog_mod.list_apps()
    states = catalog_mod.connection_states(user_id, [a["slug"] for a in apps]) if user_id else {}
    return [
        AppResponse(
            slug=a["slug"],
            name=a["name"],
            description=a["description"],
            logo=a["logo"],
            state=states.get(a["slug"], "none"),
        )
        for a in apps
    ]


@router.get(
    "/apps/status",
    summary="Catalog reachability",
    description="Public. Explains whether the app catalog is available and, if not, why.",
)
def apps_status():
    st = catalog_mod.catalog_status()
    return {
        "configured": st["configured"] == "true",
        "reachable": st["reachable"] == "true",
        "app_count": int(st["app_count"]),
        "reason": st["reason"],
    }


@router.get(
    "/integrations",
    response_model=list[ConnectionResponse],
    summary="My connections",
    description="Apps the current user has connected, with per-app state.",
)
def list_connections(
    user_id: str = Depends(get_current_user),
):
    apps = catalog_mod.list_apps()
    states = catalog_mod.connection_states(user_id, [a["slug"] for a in apps])
    out = []
    for a in apps:
        state = states.get(a["slug"], "none")
        if state == "none":
            # Only surface what is actually connected (or mid-flight).
            continue
        out.append(
            ConnectionResponse(
                provider=a["slug"], name=a["name"], connected=state == "active", state=state
            )
        )
    return out


@router.post(
    "/integrations/{provider}/connect",
    response_model=dict,
    summary="Start connecting an app",
    description=(
        "Begins the Composio OAuth flow and returns a sign-in URL. The user must "
        "approve access before the assistant can act on the app."
    ),
)
def connect(
    provider: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
    org_id: str | None = Query(
        default=None, description="Business scope; omit for a personal link."
    ),
):
    """Start OAuth for an app. The user authorises it in the returned URL."""
    try:
        out = composio_client.initiate_connection(user_id, provider, org_id=org_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except ComposioError as exc:
        raise _composio_http_error(exc)
    repositories.create_audit_event(
        db,
        event_type="integration.connect_started",
        user_id=user_id,
        details={"provider": provider, "org_id": org_id or ""},
    )
    return {"provider": provider, "redirect_url": out["redirect_url"]}


@router.get(
    "/integrations/tools",
    response_model=list[ToolkitToolsResponse],
    summary="Tools for every connected app",
    description="One row per connected app with its tool list, so the UI needs a single call.",
)
def all_toolkit_tools(
    limit: int = Query(default=200, ge=1, le=500),
    user_id: str = Depends(get_current_user),
):
    """Tool inventory for all of the caller's connections."""
    states = catalog_mod.connection_states(user_id, [])
    rows: list[ToolkitToolsResponse] = []
    try:
        connections = [
            app for app in catalog_mod.list_apps()
            if app["slug"] in states
        ]
    except Exception as exc:
        raise _composio_http_error(exc) if isinstance(exc, ComposioError) else HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not list apps: {exc}"
        )
    for app in connections:
        state = states.get(app["slug"], "none")
        try:
            tools = catalog_mod.list_tools(app["slug"], limit=limit)
        except Exception:
            tools = []
        rows.append(
            ToolkitToolsResponse(
                provider=app["slug"],
                connected=state == "active",
                state=state,
                tool_count=len(tools),
                tools=[ToolInfo(**t) for t in tools],
            )
        )
    return rows


@router.get(
    "/integrations/{provider}/insights",
    summary="Dashboard summary for an app",
    description="Read-only rollup of one connected app for the dashboard.",
)
def integration_insights(
    provider: str,
    user_id: str = Depends(get_current_user),
):
    """Summarise one connected app using read-classified tools only."""
    try:
        app = composio_client.toolkit_slug(provider)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    from pa.services.app_insights import app_insights

    state = composio_client.connection_state(user_id, app)
    if state != "active":
        return {
            "app": app,
            "connected": False,
            "state": state,
            "available": False,
            "headline": "",
            "stats": [],
            "items": [],
        }
    return {**app_insights(user_id, app), "connected": True, "state": state}


@router.get(
    "/integrations/{provider}/tools",
    response_model=ToolkitToolsResponse,
    summary="Tools available for an app",
    description=(
        "The Composio tools the agent may call for this toolkit. Requires an "
        "approved connection: `connected` reflects whether it is usable."
    ),
)
def toolkit_tools(
    provider: str,
    limit: int = Query(default=200, ge=1, le=500),
    user_id: str = Depends(get_current_user),
):
    """List the callable tools for one of the caller's connected apps."""
    try:
        slug = composio_client.toolkit_slug(provider)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    state = composio_client.connection_state(user_id, slug)
    try:
        tools = catalog_mod.list_tools(slug, limit=limit)
    except Exception as exc:
        raise _composio_http_error(exc) if isinstance(exc, ComposioError) else HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not list tools: {exc}"
        )
    return ToolkitToolsResponse(
        provider=slug,
        connected=state == "active",
        state=state,
        tool_count=len(tools),
        tools=[ToolInfo(**t) for t in tools],
    )


@router.get(
    "/integrations/{provider}/status",
    summary="Connection state for an app",
    description="active | pending | none | unavailable.",
)
def connection_status(provider: str, user_id: str = Depends(get_current_user)):
    try:
        state = composio_client.connection_state(user_id, provider)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return {"provider": provider, "state": state}


@router.delete(
    "/integrations/{provider}",
    summary="Disconnect an app",
    description="Revokes the user's Composio connection and records an audit event.",
)
def disconnect(
    provider: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
    org_id: str | None = Query(
        default=None, description="Business scope; omit for a personal link."
    ),
):
    try:
        revoked = composio_client.revoke_connection(user_id, provider, org_id=org_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    repositories.create_audit_event(
        db,
        event_type="integration.disconnected",
        user_id=user_id,
        details={"provider": provider, "revoked": revoked},
    )
    return {"provider": provider, "revoked": revoked}
