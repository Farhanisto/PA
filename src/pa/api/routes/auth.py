"""Auth routes: signup, login, logout, current user, permissions."""

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from pa.api.dependencies import SESSION_COOKIE, get_current_user, get_db
from pa.auth import (
    ALL_PERMISSIONS,
    AuthError,
    authenticate,
    create_user,
    get_permissions,
    resolve_session,
    revoke_all_sessions,
    revoke_session,
    set_permission,
)
from pa.auth.passwords import SESSION_TTL_SECONDS
from pa.db.models import User
from pa.schemas.auth import (
    LoginRequest,
    PermissionUpdate,
    SessionResponse,
    SignupRequest,
    UserResponse,
)

router = APIRouter()


def _user_response(db: Session, user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        permissions=get_permissions(db, user_id=user.id),
    )


def _issue(response: Response, token: str) -> None:
    """Set the session cookie (httpOnly + SameSite=Lax for the SPA)."""
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=False,  # local http; set True behind HTTPS
        path="/",
    )


@router.post(
    "/auth/signup",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
)
def signup(
    body: SignupRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    """Register a new user and start a session."""
    try:
        user, token = create_user(
            db, email=body.email, password=body.password, display_name=body.display_name
        )
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    _issue(response, token)
    return SessionResponse(
        access_token=token,
        expires_in=SESSION_TTL_SECONDS,
        user=_user_response(db, user),
    )


@router.post("/auth/login", response_model=SessionResponse, summary="Log in")
def login(
    body: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    """Exchange credentials for a session token."""
    try:
        user, token = authenticate(db, email=body.email, password=body.password)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )
    _issue(response, token)
    return SessionResponse(
        access_token=token,
        expires_in=SESSION_TTL_SECONDS,
        user=_user_response(db, user),
    )


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Log out")
def logout(
    response: Response,
    db: Session = Depends(get_db),
    pa_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
):
    """Revoke the current session."""
    if pa_session:
        revoke_session(db, pa_session)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return None


@router.get("/auth/me", response_model=UserResponse, summary="Current user")
def me(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the authenticated user and their permissions."""
    user = db.get(User, user_id)
    if user is None:
        # A valid session always has a user row; anything else is not a session.
        raise HTTPException(status_code=401, detail="session user no longer exists")
    return _user_response(db, user)


@router.get("/auth/permissions", response_model=dict[str, bool], summary="List permissions")
def list_permissions(user_id: str = Depends(get_current_user), db: Session = Depends(get_db)):
    return get_permissions(db, user_id=user_id)


@router.put("/auth/permissions/{key}", response_model=dict[str, bool], summary="Set a permission")
def update_permission(
    key: str,
    body: PermissionUpdate,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if key not in ALL_PERMISSIONS:
        raise HTTPException(status_code=404, detail=f"Unknown permission: {key}")
    try:
        return set_permission(db, user_id=user_id, key=key, granted=body.granted)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/auth/sessions/revoke-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Log out everywhere",
)
def revoke_all(user_id: str = Depends(get_current_user), db: Session = Depends(get_db)):
    revoke_all_sessions(db, user_id=user_id)
    return None
