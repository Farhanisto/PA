"""Execute Composio tools on behalf of a user, safely.

Rules enforced here (not in the prompt, because prompts are not a boundary):

- Only tools belonging to apps *this user has actively connected* may run.
- Arguments are validated against the tool's own JSON schema before sending.
- Read-only tools run immediately; anything that writes is classified as a
  write and must go through the approval gate.
- Execution always carries both ``connected_account_id`` and ``user_id``;
  Composio rejects one without the other.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Verbs that only read. Everything else is treated as a write and gated.
READ_VERBS = frozenset({
    "LIST", "GET", "SEARCH", "QUERY", "FIND", "LOOKUP", "DESCRIBE",
    "RETRIEVE", "FETCH", "READ", "VIEW", "EXPORT", "COUNT", "CHECK",
    "GET_CONTACT_BY_ID",
})

# Verbs that clearly mutate. Listed explicitly for documentation/audit even
# though the default is "anything not read-only is a write".
WRITE_VERBS = frozenset({
    "CREATE", "UPDATE", "DELETE", "ARCHIVE", "SEND", "POST", "MERGE",
    "CONVERT", "MOVE", "ADD", "REMOVE", "LOG", "IMPORT", "BATCH_UPDATE",
    "BATCH_DELETE", "BATCH_ARCHIVE", "BATCH_CREATE", "BATCH_SEND",
    "REPLY", "FOLLOW", "ENROLL", "PUBLISH", "SCHEDULE", "CANCEL",
})

MAX_ARGS_BYTES = 20000

# A safety cap on the text form of a result. It is generous enough for a
# real tool response (a list of CRM records), because truncating a JSON
# payload mid-string produces text that no longer parses.
MAX_RESULT_CHARS = 40000


class ToolError(RuntimeError):
    """Raised when a tool cannot be safely run."""


def classify(slug: str) -> str:
    """'read' or 'write'. Default is 'write' so unknown tools are gated."""
    upper = slug.upper()
    parts = set(upper.split("_"))
    if parts & WRITE_VERBS:
        return "write"
    if upper in READ_VERBS or parts & READ_VERBS:
        return "read"
    return "write"


def tool_index(user_id: str) -> dict[str, dict[str, Any]]:
    """Runnable tools for this user's *connected* apps.

    Returns {SLUG: {"app": ..., "description": ..., "required": [...],
    "properties": {...}, "classification": "read"|"write"}}.
    """
    from pa.integrations import catalog, composio_client

    try:
        states = composio_client.connection_states(user_id)
    except Exception as exc:
        logger.warning("tool index: states unavailable: %s", type(exc).__name__)
        return {}
    if not states:
        return {}

    active = [slug for slug, state in states.items() if state == "active"]
    index: dict[str, dict[str, Any]] = {}
    for app in active:
        for tool in _tools_with_schema(app):
            index[tool["slug"]] = {
                "app": app,
                "description": tool.get("description", ""),
                "required": tool.get("required", []),
                "properties": tool.get("properties", {}),
                "classification": classify(tool["slug"]),
            }
    return index


def _tools_with_schema(app: str, limit: int = 300) -> list[dict[str, Any]]:
    """Tool list for an app, enriched with each tool's input parameters."""
    from pa.integrations import composio_client

    try:
        client = composio_client.get_client()
        resp = client.client.tools.list(toolkit_slug=app, limit=limit)
    except Exception as exc:
        logger.warning("tool list for %s failed: %s", app, type(exc).__name__)
        return []

    out: list[dict[str, Any]] = []
    for item in getattr(resp, "items", None) or []:
        slug = str(getattr(item, "slug", "") or "")
        if not slug or getattr(item, "is_deprecated", False):
            continue
        params = getattr(item, "input_parameters", None) or {}
        if not isinstance(params, dict):
            params = {}
        out.append(
            {
                "slug": slug,
                "name": str(getattr(item, "name", None) or slug),
                "description": str(getattr(item, "description", "") or "")[:1000],
                "required": [str(x) for x in (params.get("required") or [])],
                "properties": params.get("properties") or {},
            }
        )
    return out


def _coerce(value: Any, spec: dict[str, Any]) -> Any:
    """Light coercion so "5" can satisfy an integer parameter."""
    if not isinstance(value, str) or not isinstance(spec, dict):
        return value
    kind = spec.get("type")
    try:
        if kind == "integer":
            return int(value)
        if kind == "number":
            return float(value)
        if kind == "boolean":
            return value.strip().lower() in {"1", "true", "yes", "y"}
        if kind == "array":
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else value
        if kind == "object":
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else value
    except (ValueError, TypeError):
        return value
    return value


def validate(index: dict[str, dict], slug: str, args: Any) -> dict[str, Any]:
    """Validate args against the tool's schema. Raises ToolError on problems."""
    if slug not in index:
        raise ToolError(
            f"{slug} is not available. It must belong to one of your connected apps."
        )
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolError("Tool arguments must be a JSON object.")

    spec = index[slug]
    props: dict[str, Any] = spec.get("properties") or {}
    allowed = set(props) | set(spec.get("required") or [])

    unknown = [k for k in args if k not in allowed]
    if unknown:
        raise ToolError(
            f"Unknown argument(s) for {slug}: {', '.join(sorted(unknown)[:5])}. "
            f"Allowed: {', '.join(sorted(allowed)[:12]) or 'none'}"
        )

    coerced = {k: _coerce(v, props.get(k) or {}) for k, v in args.items()}
    missing = [r for r in (spec.get("required") or []) if not coerced.get(r)]
    if missing:
        raise ToolError(
            f"{slug} is missing required argument(s): {', '.join(missing[:6])}."
        )

    if len(json.dumps(coerced, default=str)) > MAX_ARGS_BYTES:
        raise ToolError("Tool arguments are too large.")
    return coerced


def execute(user_id: str, slug: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run a tool against the caller's own connected account."""
    from pa.integrations import composio_client

    client = composio_client.get_client()
    states = composio_client.connection_states(user_id)
    if states is None:
        raise ToolError("Could not verify your app connections. Try again shortly.")
    if not any(state == "active" for state in states.values()):
        raise ToolError("You have no connected apps yet. Connect one in the Apps section.")

    ref = composio_client.user_ref(user_id)
    account_id = _account_for(client, ref, slug)
    if not account_id:
        raise ToolError(
            f"No active {slug.split('_')[0].lower()} connection. Reconnect it in Apps."
        )

    try:
        resp = client.client.tools.execute(
            tool_slug=slug,
            arguments=args,
            connected_account_id=account_id,
            user_id=ref,
        )
    except Exception as exc:
        raise ToolError(f"{type(exc).__name__}: {str(exc)[:300]}") from exc

    payload = resp if isinstance(resp, dict) else getattr(resp, "data", {}) or {}
    try:
        text = json.dumps(payload, default=str)
    except (TypeError, ValueError):
        text = str(payload)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + " … (truncated)"
    # ``payload`` is the untruncated response, for callers that need structure
    # (the assistant turns it into cards). It is popped before the result
    # enters graph state, so it is never serialised or persisted raw.
    return {"tool": slug, "result": text, "payload": payload}


def _account_for(client, user_ref: str, tool_slug: str) -> str | None:
    """The active connected-account id whose toolkit matches the tool."""
    from pa.integrations import composio_client

    prefix = tool_slug.split("_")[0].lower()
    try:
        resp = client.client.connected_accounts.list(user_ids=[user_ref], limit=100)
    except Exception as exc:
        logger.warning("account lookup failed: %s", type(exc).__name__)
        return None
    fallback = None
    for item in getattr(resp, "items", None) or []:
        if composio_client._norm_status(item) != "ACTIVE":
            continue
        cid = composio_client._pick_id(item)
        if not cid:
            continue
        toolkit = getattr(item, "toolkit", None)
        slug = getattr(toolkit, "slug", None) if not isinstance(toolkit, str) else toolkit
        slug = str(slug or "").lower()
        if slug and slug.startswith(prefix[:4]):
            return cid
        fallback = fallback or cid
    return fallback


def fit_args(meta: dict, args: dict) -> dict:
    """Keep only the arguments a tool actually accepts.

    Tool parameters get renamed and removed between SDK versions, and a
    single stale argument makes the whole call fail with "Unknown
    argument(s)". Dropping the ones the tool no longer has keeps the rest
    working instead of breaking the feature.
    """
    props = meta.get("properties") or {}
    if not props:
        return args
    return {k: v for k, v in args.items() if k in props}


def execute_read(user_id: str, slug: str, args: dict | None = None) -> Any:
    """Run a read tool for the caller's own account and return its payload.

    Used for lookups that enrich a result (resolving a stage id to its
    name). Write tools are refused outright: a lookup that quietly mutated a
    record would be a nasty surprise.

    Arguments are fitted to what the tool accepts, so a parameter that was
    renamed between SDK versions is dropped rather than failing the call.
    """
    index = tool_index(user_id)
    if slug not in index:
        raise ToolError(f"{slug} is not available for your connected apps.")
    if index[slug].get("classification") != "read":
        raise ToolError(f"{slug} is not a read tool.")
    fitted = fit_args(index[slug], args or {})
    try:
        checked = validate(index, slug, fitted)
    except ToolError:
        # Fall back to the tool's defaults.
        checked = validate(index, slug, {})
    return execute(user_id, slug, checked).get("payload")


def parse_tool_call(text: str) -> dict[str, Any] | None:
    """Pull a tool call out of model output, tolerating code fences."""
    if not text:
        return None
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", cleaned, re.S)
    if fence:
        cleaned = fence.group(1).strip()
    start = cleaned.find("{")
    if start == -1:
        return None
    end = cleaned.rfind("}")
    if end <= start:
        return None
    try:
        data = json.loads(cleaned[start : end + 1])
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("action") != "tool" or not data.get("tool"):
        return None
    return {"tool": str(data["tool"]), "args": data.get("args") or {}}