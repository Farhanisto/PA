"""Integration: a token-budget failure is retried, not dropped."""

import itertools
import json

import pytest

from pa.db import repositories
from pa.db.database import get_session_factory

_SEQ = itertools.count(1)


def _db():
    return get_session_factory()()


@pytest.fixture()
def account(client):
    email = f"budget{next(_SEQ)}@test.com"
    r = client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": "Str0ngPassw0rd"},
    )
    assert r.status_code == 201, r.text
    return {"Cookie": f"pa_session={r.json()['access_token']}"}


class _Msg:
    def __init__(self, content):
        self.content = content


class _BudgetError(Exception):
    """Shaped like the provider's 413."""

    status_code = 413

    def __init__(self):
        super().__init__(
            "Error code: 413 - {'error': {'message': 'Request too large for model', "
            "'type': 'tokens', 'code': 'rate_limit_exceeded'}}"
        )


class _Model:
    """Fails with a 413 the first time, then succeeds."""

    def __init__(self, failures: int = 1):
        self.failures = failures
        self.calls = 0

    def invoke(self, prompt, **_kw):
        self.calls += 1
        if self.calls <= self.failures:
            raise _BudgetError()
        return _Msg("All good.")


def test_a_budget_failure_is_retried_rather_than_dropped(client, account):
    """A 413 is a per-minute budget: the same request works moments later.

    Celery is eager in tests, so the retry surfaces as a ``Retry`` exception
    rather than a rescheduled task — which is exactly what a real broker
    would turn into a later attempt.
    """
    from celery.exceptions import Retry

    from pa.queue import tasks as q_tasks

    model = _Model(failures=99)
    q_tasks.set_model_override(model)
    try:
        r = client.post(
            "/api/v1/assistant/tasks",
            json={"request": "show my tickets"},
            headers=account,
        )
        job_id = r.json()["job_id"]

        db = _db()
        try:
            job = repositories.get_job(db, job_id)
            if job.status == "queued":
                db.close()
                with pytest.raises(Retry) as caught:
                    q_tasks.run_assistant_job.apply(args=[job_id])
                # It waits for the window to refill, not a couple of seconds.
                assert caught.value.countdown >= 30
                assert "status=413" in str(caught.value.exc)
        finally:
            db.close()
    finally:
        q_tasks.clear_model_override()


def test_a_permanent_failure_still_fails_with_an_actionable_message(client, account):
    """A 400 is not retryable, and the user must be told why."""
    from pa.queue import tasks as q_tasks

    class _BadRequest(Exception):
        status_code = 400

        def __init__(self):
            super().__init__("Error code: 400 - {'error': {'message': 'bad request'}}")

    class _Model:
        def __init__(self):
            self.calls = 0

        def invoke(self, prompt, **_kw):
            self.calls += 1
            raise _BadRequest()

    model = _Model()
    q_tasks.set_model_override(model)
    try:
        r = client.post(
            "/api/v1/assistant/tasks",
            json={"request": "show my tickets"},
            headers=account,
        )
        job_id = r.json()["job_id"]

        db = _db()
        try:
            job = repositories.get_job(db, job_id)
            if job.status == "queued":
                db.close()
                q_tasks.run_assistant_job.apply(args=[job_id])
                db = _db()
                job = repositories.get_job(db, job_id)
            assert job.status == "failed", job.status
            assert job.error_code
        finally:
            db.close()

        # The user sees a sentence, not a provider stack trace.
        assert "APIStatusError" not in job.error_code
        assert "Error code:" not in job.error_code
        assert model.calls == 1, "a permanent failure must not be retried"
    finally:
        q_tasks.clear_model_override()
