"""Persistent LangGraph checkpointer (SQLite file, survives restarts)."""

import os
from collections.abc import Iterator
from contextlib import contextmanager


def _saver_class():
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver

        return SqliteSaver
    except ImportError:
        from langgraph_checkpoint_sqlite import SqliteSaver  # type: ignore

        return SqliteSaver


def get_checkpoint_db_path(db_path: str | None = None) -> str:
    if db_path:
        return db_path
    return os.getenv("LANGGRAPH_CHECKPOINT_DB", "./pa_checkpoints.db")


@contextmanager
def get_checkpointer(db_path: str | None = None) -> Iterator:
    """Yield a persistent SQLite checkpointer, creating tables on first use.

    NOTE: SqliteSaver.from_conn_string() is itself a context manager in
    langgraph-checkpoint-sqlite >= 2.x, so we must enter it here.
    """
    Saver = _saver_class()
    path = get_checkpoint_db_path(db_path)
    # Ensure parent dir exists for relative/absolute file paths (not :memory:).
    if path != ":memory:":
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
    with Saver.from_conn_string(path) as saver:
        # Newer versions require explicit setup(); older do it automatically.
        setup = getattr(saver, "setup", None)
        if callable(setup):
            try:
                setup()
            except Exception:
                pass
        yield saver
