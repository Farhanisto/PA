"""Signup / login / session lifecycle for multi-user PA.

Auth is deliberately small and dependency-free:
- Opaque session tokens (see ``pa.auth.passwords``); DB stores only hashes.
- Sessions expire and can be revoked (logout / revoke-all).
- Every successful and failed attempt is written to the audit trail, but
  never with the password or raw token.
"""

from datetime import UTC, datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pa.auth.passwords import (
    SESSION_TTL_SECONDS,
    hash_password,
    hash_token,
    new_session_token,
    normalize_email,
    validate_password,
    verify_password,
)
from pa.db.models import AuditEvent, Permission, Session as SessionModel, User

# Permission keys (deny-by-default when a key is absent).
PERM_SEND_EMAIL = "email.send"
PERM_READ_INBOX = "email.read"
PERM_PROACTIVE = "email.proactive"
PERM_AUTO_SEND = "email.auto_send"

ALL_PERMISSIONS = (PERM_SEND_EMAIL, PERM_READ_INBOX, PERM_PROACTIVE, PERM_AUTO_SEND)

# Safe defaults: a new user can read, draft, and approve sends, but can never
# send without explicit approval (auto_send stays off).
DEFAULT_PERMISSIONS = {
    PERM_SEND_EMAIL: True,
    PERM_READ_INBOX: True,
    PERM_PROACTIVE: True,
    PERM_AUTO_SEND: False,
}


class AuthError(Exception):
    """Authentication failure. Message is safe to return to the client."""


def _audit(
    db: Session, *, user_id: Optional[str], event_type: str, details: Optional[dict] = None
) -> None:
    import json

    db.add(
        AuditEvent(
            user_id=user_id,
            event_type=event_type,
            details=json.dumps(details or {}),
        )
    )


def create_user(
    db: Session, *, email: str, password: str, display_name: str = ""
) -> tuple[User, str]:
    """Register a user. Returns (user, session_token). Raises AuthError."""
    try:
        normalized = normalize_email(email)
    except ValueError as exc:
        raise AuthError(str(exc)) from exc
    try:
        validate_password(password)
    except Exception as exc:
        raise AuthError(str(exc)) from exc

    user = User(
        email=normalized,
        password_hash=hash_password(password),
        display_name=(display_name or "").strip()[:128],
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AuthError("An account with that email already exists.") from exc

    for key, granted in DEFAULT_PERMISSIONS.items():
        db.add(Permission(user_id=user.id, key=key, value="", granted=granted))

    _audit(db, user_id=user.id, event_type="auth.signup", details={"email": normalized})
    db.commit()
    db.refresh(user)

    token, _ = issue_session(db, user_id=user.id)
    return user, token


def authenticate(db: Session, *, email: str, password: str) -> tuple[User, str]:
    """Verify credentials and issue a session. Raises AuthError."""
    try:
        normalized = normalize_email(email)
    except ValueError:
        # Don't leak whether the email is well-formed to an unauthenticated caller.
        raise AuthError("Invalid email or password.") from None
    user = db.scalars(select(User).where(User.email == normalized)).first()
    if user is None or not user.is_active:
        # Still do a dummy verify to reduce enumeration via timing.
        verify_password(password, hash_password("dummy-placeholder"))
        _audit(db, user_id=None, event_type="auth.login_failed", details={"email": normalized})
        db.commit()
        raise AuthError("Invalid email or password.")
    if not verify_password(password, user.password_hash):
        _audit(db, user_id=user.id, event_type="auth.login_failed", details={"email": normalized})
        db.commit()
        raise AuthError("Invalid email or password.")

    _audit(db, user_id=user.id, event_type="auth.login", details={"email": normalized})
    db.commit()
    token, _ = issue_session(db, user_id=user.id)
    return user, token


def issue_session(db: Session, *, user_id: str) -> tuple[str, SessionModel]:
    """Create an opaque session token and persist its hash."""
    token = new_session_token()
    session = SessionModel(
        user_id=user_id,
        token_hash=hash_token(token),
        expires_at=datetime.now(UTC) + timedelta(seconds=SESSION_TTL_SECONDS),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return token, session


def resolve_session(db: Session, token: str) -> Optional[User]:
    """Return the active user for a bearer token, or None."""
    if not token:
        return None
    now = datetime.now(UTC).replace(tzinfo=None)
    session = db.scalars(
        select(SessionModel).where(SessionModel.token_hash == hash_token(token))
    ).first()
    if session is None or session.revoked:
        return None
    expires = session.expires_at
    if expires.tzinfo is not None:
        expires = expires.replace(tzinfo=None)
    if expires <= now:
        return None
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        return None
    return user


def revoke_session(db: Session, token: str) -> bool:
    session = db.scalars(
        select(SessionModel).where(SessionModel.token_hash == hash_token(token))
    ).first()
    if session is None or session.revoked:
        return False
    session.revoked = True
    _audit(db, user_id=session.user_id, event_type="auth.logout")
    db.commit()
    return True


def revoke_all_sessions(db: Session, *, user_id: str) -> int:
    sessions = db.scalars(
        select(SessionModel).where(SessionModel.user_id == user_id, SessionModel.revoked.is_(False))
    ).all()
    for s in sessions:
        s.revoked = True
    _audit(
        db, user_id=user_id, event_type="auth.revoke_all_sessions", details={"count": len(sessions)}
    )
    db.commit()
    return len(sessions)


# --- Permissions ---


def get_permissions(db: Session, *, user_id: str) -> dict[str, bool]:
    rows = db.scalars(select(Permission).where(Permission.user_id == user_id)).all()
    return {p.key: bool(p.granted) for p in rows}


def permission_granted(db: Session, *, user_id: str, key: str) -> bool:
    """Deny-by-default: unknown or absent keys are not granted."""
    perm = db.scalars(
        select(Permission).where(Permission.user_id == user_id, Permission.key == key)
    ).first()
    return bool(perm and perm.granted)


def set_permission(db: Session, *, user_id: str, key: str, granted: bool) -> dict[str, bool]:
    if key not in ALL_PERMISSIONS:
        raise AuthError(f"Unknown permission: {key}")
    perm = db.scalars(
        select(Permission).where(Permission.user_id == user_id, Permission.key == key)
    ).first()
    if perm is None:
        perm = Permission(user_id=user_id, key=key, value="", granted=granted)
        db.add(perm)
    else:
        perm.granted = granted
    _audit(
        db,
        user_id=user_id,
        event_type="permission.updated",
        details={"key": key, "granted": granted},
    )
    db.commit()
    return get_permissions(db, user_id=user_id)
