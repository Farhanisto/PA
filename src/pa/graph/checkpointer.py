"""Persistent LangGraph checkpointer (Postgres, SQLite fallback).

Postgres (`postgresql://...`) uses PostgresSaver — the production path and
the default in Docker. Anything else is treated as a SQLite file path and
uses SqliteSaver (local dev / tests). Both survive restarts; never use an
in-memory checkpointer for durable workflows.
"""

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)


def is_postgres_url(url: str) -> bool:
    return url.startswith("postgres")


def to_psycopg_conninfo(url: str) -> str:
    """Strip the SQLAlchemy driver part: postgresql+psycopg://... → postgresql://..."""
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://") :]
    return url


def _sqlite_saver_class():
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver

        return SqliteSaver
    except ImportError:
        from langgraph_checkpoint_sqlite import SqliteSaver  # type: ignore

        return SqliteSaver


def _postgres_saver_class():
    try:
        from langgraph.checkpoint.postgres import PostgresSaver

        return PostgresSaver
    except ImportError:
        from langgraph_checkpoint_postgres import PostgresSaver  # type: ignore

        return PostgresSaver


def get_checkpoint_db_path(db_path: str | None = None) -> str:
    if db_path:
        return db_path
    return os.getenv("LANGGRAPH_CHECKPOINT_DB", "./pa_checkpoints.db")


@contextmanager
def get_checkpointer(db_path: str | None = None) -> Iterator:
    """Yield a persistent checkpointer.

    Schema setup runs once per process (see ensure_schema) — never per
    invocation: Postgres runs migrations with CREATE INDEX CONCURRENTLY,
    which deadlocks against snapshots held by concurrent transactions.

    NOTE: both savers' from_conn_string() are themselves context managers,
    so we must enter them here.
    """
    path = get_checkpoint_db_path(db_path)
    ensure_schema(path)
    if is_postgres_url(path):
        Saver = _postgres_saver_class()
        with Saver.from_conn_string(to_psycopg_conninfo(path)) as saver:
            yield saver
        return
    Saver = _sqlite_saver_class()
    # Ensure parent dir exists for relative/absolute file paths (not :memory:).
    if path != ":memory:":
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
    with Saver.from_conn_string(path) as saver:
        yield saver


_ensured: set[str] = set()


def ensure_schema(db_path: str | None = None) -> None:
    """Create checkpointer tables if needed — once per process per path."""
    path = get_checkpoint_db_path(db_path)
    if path in _ensured:
        return
    try:
        if is_postgres_url(path):
            Saver = _postgres_saver_class()
            with Saver.from_conn_string(to_psycopg_conninfo(path)) as saver:
                _maybe_setup(saver)
        else:
            if path != ":memory:":
                parent = os.path.dirname(os.path.abspath(path))
                os.makedirs(parent, exist_ok=True)
            Saver = _sqlite_saver_class()
            with Saver.from_conn_string(path) as saver:
                _maybe_setup(saver)
    except Exception as exc:
        logger.warning("checkpointer schema setup failed: %s", type(exc).__name__)
    finally:
        _ensured.add(path)


def reset_schema_cache() -> None:
    """Test helper: allow ensure_schema to run again."""
    _ensured.clear()


def _maybe_setup(saver) -> None:
    # Newer versions require explicit setup(); older do it automatically.
    setup = getattr(saver, "setup", None)
    if callable(setup):
        try:
            setup()
        except Exception:
            pass
