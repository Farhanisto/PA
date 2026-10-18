"""SQLAlchemy engine / session management.

No global connections are opened at import time. Call init_db() from the
FastAPI lifespan or worker startup.
"""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from pa.config import get_settings

# Migrations ship inside the package so they work from an installed wheel.
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        url = settings.DATABASE_URL
        if url.startswith("sqlite"):
            _engine = create_engine(url, connect_args={"check_same_thread": False}, future=True)
        else:
            # Postgres (psycopg): pre-ping drops stale pooled connections.
            _engine = create_engine(url, pool_pre_ping=True, future=True)
    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(), autoflush=False, autocommit=False, future=True
        )
    return _SessionLocal


def get_db():
    """FastAPI dependency yielding a DB session."""
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()


def run_migrations() -> None:
    """Apply Alembic migrations up to head.

    Self-contained: builds the Alembic config in code from the packaged
    migrations directory and the app settings, so it behaves identically in
    the repo and in a container. Idempotent — a no-op once at head.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", get_settings().DATABASE_URL)
    command.upgrade(cfg, "head")


def init_db() -> None:
    """Ensure the schema exists.

    Uses Alembic in normal operation. ``PA_SKIP_MIGRATIONS=1`` falls back to
    ``create_all`` (used by the fast in-memory/unit-test path).
    """
    import os

    # Import models so tables are registered before create_all.
    from pa.db import models  # noqa: F401

    if os.getenv("PA_SKIP_MIGRATIONS", "0") == "1":
        Base.metadata.create_all(bind=get_engine())
        return
    run_migrations()


def reset_engine() -> None:
    """Test helper to drop cached engine/session (e.g. after changing DATABASE_URL)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
