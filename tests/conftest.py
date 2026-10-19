"""Shared test fixtures: isolated SQLite DB, eager Celery, fake model."""

import os
import tempfile
from types import SimpleNamespace

import pytest

# Set test env BEFORE importing pa modules.
_tmpdir = tempfile.mkdtemp(prefix="pa-test-")
_test_db = os.path.join(_tmpdir, "test.db")
_test_checkpoints = os.path.join(_tmpdir, "checkpoints.db")

os.environ["DATABASE_URL"] = f"sqlite:///{_test_db}"
os.environ["LANGGRAPH_CHECKPOINT_DB"] = _test_checkpoints
os.environ["NEBIUS_API_KEY"] = "test-key"
os.environ["NEBIUS_MODEL"] = "test-model"
os.environ["COMPOSIO_API_KEY"] = "test-composio-key"
# Tests use throwaway SQLite files and create tables directly (fast); the app
# itself always uses Alembic.
os.environ["PA_SKIP_MIGRATIONS"] = "1"

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


# --- Composio test double -------------------------------------------------
#
# Production code has no offline/demo transport: it always talks to Composio.
# Tests inject a stand-in for the SDK through the existing DI hook
# (composio_client.set_client_factory) so the real code paths run unmodified.

FAKE_INBOX = [
    {
        "id": "msg-1",
        "threadId": "thread-1",
        "from": "billing@acme-supplies.example",
        "subject": "URGENT: Invoice #1042 is overdue",
        "snippet": "Your invoice is 14 days overdue. Please pay immediately to avoid service suspension.",
        "body": "Your invoice #1042 ($4,280) is 14 days overdue. Please pay by Friday, Nov 14 to avoid suspension.",
    },
    {
        "id": "msg-2",
        "threadId": "thread-2",
        "from": "sam@team.example",
        "subject": "Re: Q3 planning deck",
        "snippet": "Can you send me the updated deck by Thursday, Dec 3? Let me know when you can.",
        "body": "Can you send me the updated Q3 deck by Thursday, Dec 3? Let me know if Thursday works.",
    },
    {
        "id": "msg-3",
        "threadId": "thread-3",
        "from": "digest@industry.example",
        "subject": "Weekly newsletter: 12 things trending",
        "snippet": "This week in tech. Unsubscribe anytime with one click.",
        "body": "Top stories this week in tech and markets. Unsubscribe anytime.",
    },
]


class _FakeToolkits:
    def list(self, **_kwargs):
        # The SDK returns objects, not dicts; production reads .items off them.
        return SimpleNamespace(
            items=[
                {"slug": "gmail", "meta": {"display_name": "Gmail", "description": "Email."}},
                {"slug": "slack", "meta": {"display_name": "Slack", "description": "Messaging."}},
            ]
        )


class _FakeConnectedAccounts:
    def __init__(self, owner):
        self._owner = owner
        self.revoked: list[str] = []

    def list(self, **_kwargs):
        if not self._owner.connected:
            return SimpleNamespace(items=[])
        return SimpleNamespace(
            items=[{"id": "ca-1", "status": "ACTIVE", "toolkit": {"slug": "gmail"}}]
        )

    def revoke(self, connected_account_id):
        self.revoked.append(connected_account_id)
        self._owner.connected = False


class _FakeTools:
    def __init__(self, owner):
        self._owner = owner

    def list(self, toolkit_slug=None, **_kwargs):
        """Tool inventory for a toolkit (mirrors tools.list in the SDK)."""
        if toolkit_slug and toolkit_slug != "gmail":
            return type("R", (), {"items": []})()
        return type("R", (), {"items": list(self._owner.tools)})()

    def execute(self, *, tool_slug, arguments, connected_account_id, user_id=None):
        # Composio rejects a connected_account_id without a user_id (400), so
        # the double enforces the same contract.
        if not connected_account_id:
            raise RuntimeError("connected_account_id required")
        if not user_id:
            raise RuntimeError(
                "User ID is required with connected account. Please provide a user"
            )
        self._owner.executed.append(
            {"tool": tool_slug, "user_id": user_id, "account": connected_account_id}
        )
        if not self._owner.connected:
            raise RuntimeError("connected account is not active")
        if tool_slug.endswith("FETCH_EMAILS"):
            return {"messages": list(self._owner.inbox)}
        if tool_slug.endswith("SEND_EMAIL"):
            self._owner.sent.append(
                {"account": connected_account_id, **arguments, "thread_id": arguments.get("thread_id", "")}
            )
            return {"id": f"sent-{len(self._owner.sent)}"}
        return {}


class _FakeAuthConfigs:
    def list(self, **_kwargs):
        return SimpleNamespace(items=[{"id": "authcfg-1"}])


class _FakeLink:
    def create(self, **_kwargs):
        return SimpleNamespace(redirect_url="https://composio.test/oauth/authorize")


class _FakeSDK:
    def __init__(self, owner):
        self.toolkits = _FakeToolkits()
        self.connected_accounts = _FakeConnectedAccounts(owner)
        self.tools = _FakeTools(owner)
        self.auth_configs = _FakeAuthConfigs()
        self.link = _FakeLink()


class FakeComposio:
    """Stand-in for the Composio SDK client."""

    def __init__(self):
        self.sent: list[dict] = []
        self.executed: list[dict] = []
        def _tool(slug, name, desc, required=(), props=None, deprecated=False):
            return type(
                "T", (),
                {
                    "slug": slug,
                    "name": name,
                    "description": desc,
                    "is_deprecated": deprecated,
                    "input_parameters": {
                        "required": list(required),
                        "properties": props or {},
                    },
                },
            )()

        self.tools = [
            _tool("GMAIL_FETCH_EMAILS", "Fetch emails", "Fetch mail"),
            _tool(
                "GMAIL_SEND_EMAIL", "Send email", "Send mail",
                required=["to", "subject", "body"],
                props={
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                    "thread_id": {"type": "string"},
                },
            ),
            _tool(
                "GMAIL_CREATE_EMAIL_DRAFT", "Create draft", "Draft mail",
                required=["to", "body"],
                props={"to": {"type": "string"}, "body": {"type": "string"}},
            ),
            _tool(
                "HUBSPOT_LIST_DEALS", "List deals", "List deals in HubSpot",
                props={"limit": {"type": "integer"}, "after": {"type": "string"}},
            ),
            _tool(
                "HUBSPOT_CREATE_DEAL", "Create deal", "Create a HubSpot deal",
                required=["dealname"],
                props={"dealname": {"type": "string"}, "amount": {"type": "string"}},
            ),
        ]
        self.inbox = [dict(m) for m in FAKE_INBOX]
        self.connected = True
        self.client = _FakeSDK(self)


@pytest.fixture(autouse=True)
def fake_composio():
    from pa.integrations import catalog as catalog_mod
    from pa.integrations import composio_client

    fake = FakeComposio()
    composio_client.set_client_factory(lambda: fake)
    # These caches are module-level singletons in production; clear them so one
    # test's cached result cannot leak into the next.
    composio_client.invalidate_states()
    catalog_mod._catalog_cache["at"] = 0.0
    catalog_mod._catalog_cache["apps"] = None
    catalog_mod._tool_cache.clear()
    try:
        yield fake
    finally:
        composio_client.clear_client_factory()
        composio_client.invalidate_states()
        catalog_mod._tool_cache.clear()
