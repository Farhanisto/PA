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
    # Connected apps + their tools are the assistant's entire world; without
    # this it degrades into a generic chatbot that cannot see any app.
    if s.get("app_context") and not s.get("context"):
        updates["app_context"] = s["app_context"]
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
    """Build a prompt that keeps the assistant inside the user's app world.

    Framing matters more than length here: an unconstrained prompt makes the
    model behave like a general chatbot, so the role, the available apps, and
    the refusal rules all come before the request, and the rules are repeated
    after it where recency helps.
    """
    request = state.get("request", "")
    context = state.get("context", "")
    task_items = state.get("task_items") or []
    app_context = state.get("app_context") or ""

    rules = (
        "Rules:\n"
        "1. Only discuss work the connected apps above can actually do.\n"
        "2. If the request is unrelated to them, reply with one short sentence "
        "naming the app that would be needed, then stop. Do not answer general "
        "knowledge, trivia, coding, or small talk.\n"
        "3. Never ask the user for a password, API key, or OAuth token. "
        "Connections are managed in the Apps section, not by chat.\n"
        "4. Never invent app data. If you cannot see the data, say exactly what "
        "you would need to fetch it.\n"
        "5. Be concise."
    )

    parts = ["You are PA, an assistant that acts only through the user's connected apps."]
    if app_context:
        parts.append(app_context)
    parts.append(rules)
    parts.append(f"User request: {request}")
    if context:
        parts.append(f"User preferences:\n{context}")
    if task_items:
        parts.append("Tasks:\n" + "\n".join(f"- {x}" for x in task_items))
    parts.append(rules)
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



TOOL_DECISION_FORMAT = (
    "Reply with ONE JSON object and nothing else. Exactly one of:\n"
    'A) tool call: {"action":"tool","tool":"<EXACT_TOOL_SLUG>","args":{...}}\n'
    'B) plain reply: {"action":"reply","text":"..."}\n'
    "Use A only when a listed tool can answer the request. Never invent a tool slug."
)


def plan_action(state: dict, config: RunnableConfig | None = None) -> dict:
    """Let the assistant pick a real tool, or answer in prose.

    The tool slug is validated against the caller's own connected apps before
    anything executes; an unknown slug falls back to a plain reply.
    """
    s = _as_dict(state)
    if s.get("error") or s.get("tool_call"):
        return {}
    try:
        from pa.services.tool_runner import parse_tool_call, tool_index

        index = tool_index(s.get("user_id", ""))
    except Exception as exc:
        return {"error": f"tool_lookup_error: {type(exc).__name__}"}

    if not index:
        return {"approval_status": "not_required"}

    available = "\n".join(f"- {slug} ({meta['app']}): {meta['description'][:160]}"
                          for slug, meta in list(index.items())[:200])
    prompt = (
        f"{TOOL_DECISION_FORMAT}\n\n"
        f"Connected app tools you may call:\n{available}\n\n"
        f"User request: {s.get('request', '')}"
    )
    try:
        model = _resolve_model(config)
        raw = model.invoke(prompt) if hasattr(model, "invoke") else model(prompt)
        text = getattr(raw, "content", raw)
        text = text if isinstance(text, str) else str(text)
    except Exception as exc:
        return {"error": f"model_error: {type(exc).__name__}"}

    call = parse_tool_call(text)
    if not call:
        return {"approval_status": "not_required"}
    if call["tool"] not in index:
        return {"approval_status": "not_required"}
    call["classification"] = index[call["tool"]]["classification"]
    call["app"] = index[call["tool"]]["app"]
    return {"tool_call": call}


def run_read_tool(state: dict, config: RunnableConfig | None = None) -> dict:
    """Execute a read-only tool immediately."""
    s = _as_dict(state)
    call = s.get("tool_call") or {}
    if s.get("error") or not call:
        return {}
    if call.get("classification") != "read":
        return {}
    try:
        from pa.services.tool_runner import ToolError, execute, tool_index, validate

        index = tool_index(s.get("user_id", ""))
        args = validate(index, call["tool"], call.get("args") or {})
        result = execute(s.get("user_id", ""), call["tool"], args)
    except ToolError as exc:
        return {"tool_result": {"tool": call.get("tool"), "error": str(exc)}}
    except Exception as exc:
        return {"tool_result": {"tool": call.get("tool"), "error": f"{type(exc).__name__}"}}
    return {"tool_result": result}


def summarize_tool_result(state: dict, config: RunnableConfig | None = None) -> dict:
    """Turn a raw tool payload into a short human answer."""
    s = _as_dict(state)
    result = s.get("tool_result") or {}
    if s.get("error") or not result:
        return {}
    if result.get("error"):
        return {"response": f"That didn't work: {result['error']}"}
    raw = result.get("result", "")
    prompt = (
        f"User asked: {s.get('request', '')}\n\n"
        f"Tool `{result.get('tool')}` returned:\n{raw[:6000]}\n\n"
        "Summarise the answer in a few short lines. Mention concrete names, "
        "counts and values from the data. Do not speculate beyond it."
    )
    try:
        model = _resolve_model(config)
        out = model.invoke(prompt) if hasattr(model, "invoke") else model(prompt)
        text = getattr(out, "content", out)
        text = text if isinstance(text, str) else str(text)
    except Exception as exc:
        return {"response": f"{type(exc).__name__} while summarising the result."}
    return {"response": text.strip() or raw[:2000]}


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
    call = s.get("tool_call") or {}
    if call and call.get("classification") == "write":
        # Never let a write slip through the keyword heuristic below: it is
        # always gated, regardless of how the request was phrased.
        return {
            "proposed_action": {
                "action": "composio_tool",
                "tool": call.get("tool"),
                "app": call.get("app"),
                "args": call.get("args") or {},
                "summary": f"Run {call.get('tool')} in {call.get('app')}",
            },
            "approval_status": "pending",
        }
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
