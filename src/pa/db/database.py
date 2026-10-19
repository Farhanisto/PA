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
    try:
        command.upgrade(cfg, "head")
    except Exception as exc:
        raise RuntimeError(_migration_error(exc)) from exc


def _migration_error(exc: Exception) -> str:
    """Turn a migration failure into something the operator can act on.

    The common one is a database that is *ahead* of this build: the schema
    was migrated by a newer image, and this container does not have that
    revision's file. Nothing is wrong with the database — the image is
    stale, and only rebuilding fixes it.
    """
    text = str(exc)
    if "Can't locate revision" in text or "Can't locate these revisions" in text:
        return (
            "The database is at a revision this build does not contain "
            f"({text.strip()}). The database was migrated by a newer image than "
            "this one. Rebuild and restart the containers so the image includes "
            "the matching migrations: `docker compose up -d --build --force-recreate`"
        )
    return f"Migration failed: {type(exc).__name__}: {text[:400]}"


def verify_schema() -> None:
    """Fail loudly when the database is behind the code.

    A container running new code against an old schema otherwise fails later
    with an obscure ``UndefinedColumn`` error — or, worse, an old image
    quietly runs without the features the code expects, and nothing says so.
    """
    from sqlalchemy import inspect

    inspector = inspect(get_engine())
    problems: list[str] = []
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            problems.append(f"table {table.name} is missing")
            continue
        actual = {c["name"] for c in inspector.get_columns(table.name)}
        for col in table.columns:
            if col.name not in actual:
                problems.append(f"{table.name}.{col.name} is missing")

    if problems:
        raise RuntimeError(
            "The database schema is behind the code ("
            + ", ".join(problems[:8])
            + "). Run `alembic upgrade head` — the app applies migrations on "
            "startup, so this means the deployment is running an image whose "
            "migrations do not match the code."
        )


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
    verify_schema()


def reset_engine() -> None:
    """Test helper to drop cached engine/session (e.g. after changing DATABASE_URL)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
