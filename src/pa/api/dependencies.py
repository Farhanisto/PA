"""Auth + DB dependencies.

``get_current_user`` is the security boundary: the user id always comes from a
valid session token (httpOnly cookie or ``Authorization: Bearer``). It is never
read from a request body or from a client-chosen header.

``get_optional_user`` backs public pages so the UI can render without signing in.
"""

from collections.abc import Generator

from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from pa.auth import resolve_session
from pa.db.database import get_session_factory

SESSION_COOKIE = "pa_session"


def get_db() -> Generator[Session, None, None]:
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()


def _token_from(pa_session: str | None, authorization: str | None) -> str | None:
    if pa_session:
        return pa_session
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def get_optional_user(
    db: Session = Depends(get_db),
    pa_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    authorization: str | None = Header(default=None),
) -> str | None:
    """Caller id if a valid session exists, else None (public access)."""
    token = _token_from(pa_session, authorization)
    if not token:
        return None
    user = resolve_session(db, token)
    return user.id if user else None


def get_current_user(
    db: Session = Depends(get_db),
    pa_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    authorization: str | None = Header(default=None),
) -> str:
    """Authenticated user id. 401 when no valid session is presented."""
    token = _token_from(pa_session, authorization)
    user = resolve_session(db, token) if token else None
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to continue.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user.id
