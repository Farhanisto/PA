"""Graph nodes — each is a small, testable state transition.

Node signatures use RunnableConfig so LangGraph injects config (with our
model passed via configurable). They also work when called directly with
just state (tests).
"""

from typing import Any

try:
    from langchain_core.runnables import RunnableConfig
except ImportError:  # pragma: no cover
    RunnableConfig = dict  # type: ignore

SENSITIVE_KEYWORDS = (
    "send",
    "email",
    "message",
    "post",
    "publish",
    "delete",
    "purchase",
    "buy",
    "pay",
    "share",
    "whatsapp",
    "gmail",
    "zoom",
    "meeting",
    "schedule",
    "calendar",
    "call",
)


def _as_dict(state: Any) -> dict:
    return dict(state or {})


def understand_request(state: dict, config: RunnableConfig | None = None) -> dict:
    s = _as_dict(state)
    request = (s.get("request") or "").lower()
    if any(k in request for k in ("briefing", "morning", "daily", "tasks", "agenda")):
        workflow = "briefing"
    elif any(k in request for k in SENSITIVE_KEYWORDS):
        workflow = "action"
    else:
        workflow = "general"
    return {"workflow": workflow}


def prepare_context(state: dict, config: RunnableConfig | None = None) -> dict:
    s = _as_dict(state)
    updates: dict = {}
    # Memory snippets may be injected via state (loaded by worker with auth).
    snippets = s.get("memory_snippets") or s.get("preferences") or []
    if snippets and not s.get("context"):
        if isinstance(snippets, list):
            updates["context"] = "\n".join(str(x) for x in snippets[:10])
        elif isinstance(snippets, str):
            updates["context"] = snippets
    # Seed task_items for briefing workflow if caller provided tasks/notes.
    if s.get("workflow") == "briefing" and not s.get("task_items"):
        if isinstance(s.get("tasks"), list):
            updates["task_items"] = list(s["tasks"])[:20]
    if "task_items" not in s and "task_items" not in updates:
        updates["task_items"] = []
    return updates


def _resolve_model(config: RunnableConfig | None = None):
    if config and isinstance(config.get("configurable"), dict):
        model = config["configurable"].get("model")
        if model is not None:
            return model
    from pa.models.factory import create_model

    return create_model()


def _prompt_for(state: dict) -> str:
    request = state.get("request", "")
    context = state.get("context", "")
    task_items = state.get("task_items") or []
    parts = [f"User request: {request}"]
    if context:
        parts.append(f"User context/preferences:\n{context}")
    if task_items:
        parts.append("Tasks:\n" + "\n".join(f"- {t}" for t in task_items))
    parts.append("Respond concisely and helpfully.")
    return "\n\n".join(parts)


def generate_response(state: dict, config: RunnableConfig | None = None) -> dict:
    s = _as_dict(state)
    if s.get("error"):
        return {}
    try:
        model = _resolve_model(config)
        prompt = _prompt_for(s)
        # Support both LangChain chat models (invoke -> AIMessage) and fakes.
        result = model.invoke(prompt) if hasattr(model, "invoke") else model(prompt)
        text = getattr(result, "content", result)
        if not isinstance(text, str):
            text = str(text)
        text = text.strip()
        if not text:
            return {"error": "empty model response"}
        return {"response": text}
    except Exception as exc:  # bounded: surfaced in state, retried only for transient (see tasks)
        return {"error": f"model_error: {type(exc).__name__}: {str(exc)[:500]}"}


def verify_result(state: dict, config: RunnableConfig | None = None) -> dict:
    s = _as_dict(state)
    if s.get("error"):
        return {}
    response = (s.get("response") or "").strip()
    if not response:
        return {"error": "verification_failed: empty response"}
    if len(response) > 20000:
        return {"error": "verification_failed: response too long"}
    return {}


def propose_action(state: dict, config: RunnableConfig | None = None) -> dict:
    s = _as_dict(state)
    if s.get("error") or s.get("proposed_action"):
        return {}
    request = (s.get("request") or "").lower()
    workflow = s.get("workflow", "")
    needs = workflow == "action" or any(k in request for k in SENSITIVE_KEYWORDS)
    if not needs:
        return {"approval_status": "not_required"}
    proposal = {
        "action": "user_requested_action",
        "request": s.get("request", ""),
        "summary": (s.get("response") or "")[:1000],
    }
    return {"proposed_action": proposal, "approval_status": "pending"}


def approval_gate(state: dict, config: RunnableConfig | None = None) -> dict:
    """Pause before sensitive side effects using LangGraph interrupt."""
    s = _as_dict(state)
    if s.get("error"):
        return {}
    if not s.get("proposed_action"):
        return {"approval_status": s.get("approval_status", "not_required")}
    if s.get("approval_status") == "approved":
        return {}
    try:
        from langgraph.types import interrupt
    except ImportError:  # pragma: no cover - old langgraph without interrupt
        return {"approval_status": "pending"}
    decision = interrupt(
        {
            "type": "approval_required",
            "proposed_action": s.get("proposed_action"),
        }
    )
    # Resume value convention: {"decision": "approved"} or "approved" string.
    normalized = decision.get("decision") if isinstance(decision, dict) else decision
    if normalized == "approved":
        return {"approval_status": "approved"}
    return {"approval_status": "rejected", "error": "action rejected by user"}


def finalize(state: dict, config: RunnableConfig | None = None) -> dict:
    s = _as_dict(state)
    if s.get("error"):
        return {}
    if not (s.get("response") or "").strip():
        # Fallback for briefing workflow without model output.
        if s.get("workflow") == "briefing":
            from pa.tools.briefing import build_briefing

            draft = build_briefing(tasks=s.get("task_items"), notes=s.get("notes"))
            return {"response": draft}
        return {"error": "finalization_failed: no response"}
    # If approval was required but not granted, do not claim success.
    if s.get("proposed_action") and s.get("approval_status") not in ("approved", "not_required"):
        if s.get("approval_status") == "pending":
            return {}  # worker will mark waiting_for_approval
        return {"error": f"action not approved: {s.get('approval_status')}"}
    return {}
