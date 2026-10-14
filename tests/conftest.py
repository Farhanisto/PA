"""Shared test fixtures: isolated SQLite DB, eager Celery, fake model."""

import os
import tempfile

import pytest

# Set test env BEFORE importing pa modules.
_tmpdir = tempfile.mkdtemp(prefix="pa-test-")
_test_db = os.path.join(_tmpdir, "test.db")
_test_checkpoints = os.path.join(_tmpdir, "checkpoints.db")

os.environ["DATABASE_URL"] = f"sqlite:///{_test_db}"
os.environ["LANGGRAPH_CHECKPOINT_DB"] = _test_checkpoints
os.environ["PA_FAKE_MODEL"] = "1"
os.environ["NEBIUS_API_KEY"] = "test-key"
os.environ["NEBIUS_MODEL"] = "test-model"

from pa.config import reset_settings_cache
from pa.db.database import init_db, reset_engine
from pa.queue.celery_app import celery_app

reset_settings_cache()
reset_engine()
init_db()

celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)


class FakeModel:
    def __init__(self, text="fake response"):
        self.text = text
        self.calls = []

    def invoke(self, prompt):
        self.calls.append(prompt)

        class Msg:
            def __init__(self, content):
                self.content = content

        return Msg(self.text)


@pytest.fixture()
def fake_model():
    return FakeModel("Hello from fake model")


@pytest.fixture()
def db_session():
    reset_settings_cache()
    from pa.db.database import get_session_factory

    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from pa.app import create_app

    app = create_app()
    # Avoid lifespan DB re-init side effects; DB already initialized.
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_model_override():
    from pa.queue import tasks as q_tasks

    q_tasks.clear_model_override()
    yield
    q_tasks.clear_model_override()
