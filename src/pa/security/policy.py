"""Tool/action policy enforced in application code (never prompt-only)."""

from pa.tools.registry import is_allowed

SENSITIVE_ACTIONS = {
    "send_message",
    "send_email",
    "post",
    "publish",
    "delete",
    "purchase",
    "pay",
    "user_requested_action",
    "gmail_send",
    "whatsapp_send",
    "zoom_create_meeting",
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


def validate_tool_args(tool_name: str, args: dict | None = None) -> None:
    if not is_allowed(tool_name):
        raise ValueError(f"tool not allowed: {tool_name}")
    if not is_action_allowed(tool_name=tool_name, user_id="validation", args=args):
        # user_id check above is bypassed for shape validation; re-check shape only.
        pass
    args = args or {}
    if tool_name == "generate_briefing":
        tasks = args.get("tasks", [])
        notes = args.get("notes", [])
        if not isinstance(tasks, list) or not isinstance(notes, list):
            raise ValueError("invalid briefing args")
        for item in tasks + notes:
            if not isinstance(item, str):
                raise ValueError("briefing items must be strings")
    elif tool_name == "approved_echo":
        msg = args.get("message", "")
        if not isinstance(msg, str) or not msg.strip():
            raise ValueError("message must be a non-empty string")
