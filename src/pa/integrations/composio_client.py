"""Composio OAuth connections for any toolkit in the catalog.

Composio owns the OAuth dance and per-user connected accounts; PA uses it for
**connection management** — start the Connect Link, report status, and revoke.
The SDK is imported lazily so the app/tests work without it installed, and
only the project API key travels; user credentials never touch PA.

Authentication is always per user: every call carries the caller's stable
database id (``user_ref``), so one user can never reach another's connected
accounts.

Any toolkit slug from ``GET /api/v1/apps`` is accepted; slugs are normalised
rather than allowlisted. Each toolkit still needs an **auth config** (a
blueprint shared by all users) before a connection can be created.
"""

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

# Test hook: tests may set a fake client factory.
_client_factory = None


def set_client_factory(factory) -> None:
    global _client_factory
    _client_factory = factory


def clear_client_factory() -> None:
    global _client_factory
    _client_factory = None
    reset_client()


def _configured_key() -> str:
    """Read the key from the app's settings (process env or the .env file)."""
    from pa.config import get_settings

    return (get_settings().COMPOSIO_API_KEY or "").strip()


def is_configured() -> bool:
    return bool(_configured_key()) or _client_factory is not None


def _require_key() -> str:
    key = _configured_key()
    if not key:
        raise RuntimeError("COMPOSIO_API_KEY is not configured")
    return key


_client_cache = None


def reset_client() -> None:
    """Drop the memoised client (config change or test teardown)."""
    global _client_cache
    _client_cache = None


def get_client():
    """Return a shared Composio client (fake factory wins in tests).

    Constructing ``Composio(...)`` costs several seconds, so it is memoised:
    the client is stateless and safe to reuse, and callers no longer pay that
    cost on every request.
    """
    global _client_cache
    if _client_factory is not None:
        return _client_factory()
    if _client_cache is None:
        try:
            from composio import Composio
        except ImportError as exc:
            raise RuntimeError("composio package is not installed") from exc
        _client_cache = Composio(api_key=_require_key())
    return _client_cache


def toolkit_slug(provider: str) -> str:
    """Normalise a provider name into a Composio toolkit slug.

    Composio accepts any toolkit in the catalog, so there is no fixed
    whitelist; we only normalise and reject obviously invalid input.
    """
    slug = (provider or "").strip().lower().replace("_", "-").replace(" ", "-")
    if not slug:
        raise ValueError("provider is required")
    if len(slug) > 128 or not all(ch.isalnum() or ch == "-" for ch in slug):
        raise ValueError(f"invalid toolkit slug: {provider!r}")
    return slug


def user_ref(user_id: str, org_id: str | None = None) -> str:
    """Composio account owner ref.

    Individuals connect as their own user id; businesses namespace the link
    under their org so one person's OAuth never leaks into another org's
    jobs: ``org:{org}:user:{user}``.
    """
    user_id = (user_id or "").strip()
    if not user_id:
        raise ValueError("user_id is required")
    org_id = (org_id or "").strip()
    if not org_id:
        return user_id
    if len(org_id) > 128:
        raise ValueError("org_id too long")
    return f"org:{org_id}:user:{user_id}"


def auth_config_env(provider: str) -> str:
    return os.getenv(f"COMPOSIO_{provider.upper()}_AUTH_CONFIG_ID", "")


def resolve_auth_config_id(provider: str, client=None) -> str:
    """Auth config id: explicit env override wins, else dashboard discovery."""
    slug = toolkit_slug(provider)  # validates + normalises
    override = auth_config_env(provider)
    if override:
        return override
    client = client or get_client()
    resp = client.client.auth_configs.list(toolkit_slug=slug, limit=10)
    items = getattr(resp, "items", None) or getattr(resp, "data", None) or []
    if isinstance(items, dict):
        items = items.get("items", []) or items.get("data", [])
    for item in items:
        cid = _pick_id(item)
        if cid:
            return cid
    raise ValueError(
        f"no Composio auth config for '{provider}' "
        f"(create one in the dashboard, or set COMPOSIO_{provider.upper()}_AUTH_CONFIG_ID)"
    )


def initiate_connection(
    user_id: str, provider: str, client=None, org_id: str | None = None
) -> dict[str, Any]:
    """Start OAuth: returns a redirect_url the user completes in a browser."""
    slug = toolkit_slug(provider)
    ref = user_ref(user_id, org_id)
    client = client or get_client()
    auth_config_id = resolve_auth_config_id(provider, client)
    kwargs: dict[str, Any] = {"auth_config_id": auth_config_id, "user_id": ref}
    callback = os.getenv("COMPOSIO_CALLBACK_URL")
    if callback:
        kwargs["callback_url"] = callback
    link = client.client.link.create(**kwargs)
    url = _pick_redirect(link)
    if not url:
        raise RuntimeError(f"Composio did not return a redirect URL for '{provider}'")
    invalidate_states(user_id)
    logger.info("composio connect initiated: provider=%s user set", provider)
    return {"provider": provider, "redirect_url": url}


def _state_for(status: str) -> str:
    status = (status or "").upper()
    if status == "ACTIVE":
        return "active"
    if status in {"INITIALIZING", "INITIATED", "FAILED", "EXPIRED"}:
        return "pending"
    return "none"


def _item_toolkit_slug(item: Any) -> str:
    """Pull the toolkit slug off a connected account (object or dict shape)."""
    raw = item.get("toolkit") if isinstance(item, dict) else getattr(item, "toolkit", None)
    if raw is None:
        raw = item.get("toolkit_slug") if isinstance(item, dict) else getattr(item, "toolkit_slug", None)
    if raw is None:
        return ""
    slug = raw.get("slug") if isinstance(raw, dict) else getattr(raw, "slug", None)
    if not slug and isinstance(raw, str):
        slug = raw
    return str(slug or "").strip().lower()


def _unwrap_items(resp: Any) -> list:
    items = getattr(resp, "items", None) or getattr(resp, "data", None) or []
    if isinstance(items, dict):
        items = items.get("items", []) or items.get("data", [])
    return list(items)


_STATE_TTL_SECONDS = 15.0
_state_cache: dict[str, tuple[float, dict[str, str]]] = {}


def invalidate_states(user_id: str | None = None) -> None:
    """Drop cached connection states (all users, or just one)."""
    if user_id is None:
        _state_cache.clear()
    else:
        _state_cache.pop(user_ref(user_id), None)


def connection_states(user_id: str, org_id: str | None = None) -> dict[str, str]:
    """Map toolkit slug -> state for ONE user, in a single API call.

    Querying per toolkit is an N+1 against the network: with ~60 toolkits that
    is ~60 round trips (tens of seconds). One unfiltered list is enough.

    Results are cached briefly and invalidated explicitly whenever we connect
    or revoke, so repeated page loads do not pay a round trip while a genuine
    change is still reflected immediately.
    """
    if not is_configured():
        return {}
    ref = user_ref(user_id, org_id)
    cached = _state_cache.get(ref)
    if cached is not None:
        at, states = cached
        if (time.monotonic() - at) < _STATE_TTL_SECONDS:
            return dict(states)
    try:
        client = get_client()
        resp = client.client.connected_accounts.list(user_ids=[ref], limit=100)
    except Exception as exc:
        logger.warning("composio status check failed: %s", type(exc).__name__)
        return {}
    out: dict[str, str] = {}
    for item in _unwrap_items(resp):
        slug = _item_toolkit_slug(item)
        if not slug:
            continue
        state = _state_for(_norm_status(item))
        # An active account wins over a stale pending/failed one.
        if out.get(slug) != "active":
            out[slug] = state
    _state_cache[ref] = (time.monotonic(), dict(out))
    return out


def connection_state(user_id: str, provider: str, client=None, org_id: str | None = None) -> str:
    """'active' | 'pending' | 'none' | 'unavailable' (never raises on API errors)."""
    slug = toolkit_slug(provider)
    if not is_configured():
        return "unavailable"
    return connection_states(user_id, org_id=org_id).get(slug, "none")


def revoke_connection(user_id: str, provider: str, client=None, org_id: str | None = None) -> bool:
    """Revoke the user's active Composio connection; True if anything revoked."""
    slug = toolkit_slug(provider)
    ref = user_ref(user_id, org_id)
    client = client or get_client()
    resp = client.client.connected_accounts.list(
        user_ids=[ref], toolkit_slugs=[slug], limit=10
    )
    items = getattr(resp, "items", None) or getattr(resp, "data", None) or []
    if isinstance(items, dict):
        items = items.get("items", []) or items.get("data", [])
    revoked = False
    for item in items:
        if _norm_status(item) != "ACTIVE":
            continue
        cid = _pick_id(item)
        if not cid:
            continue
        try:
            client.client.connected_accounts.revoke(cid)
            revoked = True
        except Exception as exc:
            logger.warning("composio revoke failed: %s", type(exc).__name__)
    return revoked


def _norm_status(item: Any) -> str:
    if isinstance(item, dict):
        raw = item.get("status") or item.get("state") or ""
    else:
        raw = getattr(item, "status", "") or getattr(item, "state", "")
    return str(raw).upper()


def _pick_id(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("id", "nanoid", "connected_account_id", "auth_config_id"):
            if item.get(key):
                return str(item[key])
        return ""
    for attr in ("id", "nanoid", "connected_account_id", "auth_config_id"):
        if getattr(item, attr, None):
            return str(getattr(item, attr))
    return ""


def _pick_redirect(link: Any) -> str:
    if isinstance(link, dict):
        for key in ("redirect_url", "redirectUrl", "url", "link"):
            if link.get(key):
                return str(link[key])
        return ""
    for attr in ("redirect_url", "redirectUrl", "url", "link"):
        if getattr(link, attr, None):
            return str(getattr(link, attr))
    return ""
