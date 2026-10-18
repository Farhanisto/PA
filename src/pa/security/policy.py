"""Tool/action policy enforced in application code (never prompt-only)."""

from pa.tools.registry import is_allowed

# Actions that touch the outside world. Every one requires explicit approval.
SENSITIVE_ACTIONS = {
    "email_send_draft",
    "send_message",
    "send_email",
    "post",
    "publish",
    "delete",
    "purchase",
    "pay",
    "user_requested_action",
}

REQUIRES_APPROVAL = set(SENSITIVE_ACTIONS)


def requires_approval(action: str) -> bool:
    return action in REQUIRES_APPROVAL


def is_action_allowed(*, tool_name: str, user_id: str, args: dict | None = None) -> bool:
    if not user_id:
        return False
    if not is_allowed(tool_name):
        return False
    args = args or {}
    # Validate args are JSON-serializable primitives and bounded.
    for k, v in args.items():
        if not isinstance(k, str) or len(k) > 128:
            return False
        if isinstance(v, str) and len(v) > 5000:
            return False
        if not isinstance(v, (str, int, float, bool, list, dict, type(None))):
            return False
    return True
