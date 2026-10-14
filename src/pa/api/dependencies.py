"""Auth + DB dependencies.

MVP auth: derive user_id from authenticated principal. For local single-user
demo we accept X-User-Id (default 'demo-user') and document that this must be
replaced by real auth before exposing outside localhost.
"""

from collections.abc import Generator

from fastapi import Header
from sqlalchemy.orm import Session

from pa.db.database import get_session_factory

DEFAULT_DEMO_USER = "demo-user"


def get_current_user_id(
    x_user_id: str | None = Header(
        default=None,
        description="Caller identity for the local single-user demo (defaults to `demo-user`).",
    ),
) -> str:
    if x_user_id and x_user_id.strip():
        return x_user_id.strip()[:128]
    return DEFAULT_DEMO_USER


def get_db() -> Generator[Session, None, None]:
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()
