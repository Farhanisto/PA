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
    # The earlier turns of this conversation, so a short answer to a
    # clarifying question is understood in context.
    history = s.get("history") or []
    if history:
        updates["history"] = history
    # Pin the request to an app, and notice when the user asks for an app
    # they have not connected.
    if "target_app" not in s:
        try:
            from pa.services.app_context import resolve_app_scope

            scope = resolve_app_scope(
                s.get("request", ""),
                s.get("history") or [],
                s.get("connected_apps") or [],
            )
            updates["target_app"] = scope.get("target") or ""
            updates["unconnected_app"] = scope.get("unconnected") or ""
        except Exception:
            pass
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


def _focus_block(focus: dict) -> str:
    """Describe the record the user is acting on, for the prompt.

    The id matters most: it is what the assistant must put in the tool
    arguments so the write lands on this record and not another.
    """
    if not focus or not isinstance(focus, dict):
        return ""
    label = focus.get("type") or focus.get("kind") or "record"
    title = focus.get("title") or ""
    record_id = focus.get("record_id") or ""
    app = focus.get("app") or ""
    lines = [f"The user is acting on one specific {label.lower()}:"]
    if title:
        lines.append(f"- Title: {title}")
    if record_id:
        lines.append(f"- Id: {record_id}")
    if app:
        lines.append(f"- App: {app}")
    fields = focus.get("fields") or []
    shown = [
        f"{f.get('label')}: {f.get('value')}"
        for f in fields
        if isinstance(f, dict) and f.get("label") and f.get("value")
    ]
    if shown:
        lines.append("- Current values: " + ", ".join(shown[:10]))
    lines.append(
        f"If the request is about this {label.lower()}, use the id above in any tool "
        f"arguments that identify it, so the change lands on this record and not "
        f"another one."
    )
    return "\n".join(lines)


def _selected_skills(state: dict) -> list:
    """The skills that apply to this request."""
    try:
        from pa.services.skills import select_skills

        return select_skills(
            state.get("request", ""),
            state.get("target_app") or None,
            state.get("connected_apps") or [],
        )
    except Exception:
        return []


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
    history = state.get("history") or []
    focus = state.get("focus") or {}

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
        "5. Be concise.\n"
        "6. Read the conversation before the latest request: the user's message "
        "may be a short answer to your own question, and it inherits the app "
        "and the subject from that question.\n"
        "7. Never describe, summarise, or claim to have used an app that is not "
        "in the connected list above — not even as a suggestion of what the "
        "data would look like. If such an app is needed, say only that the "
        "user must connect it."
    )

    parts = ["You are PA, an assistant that acts only through the user's connected apps."]
    if history:
        lines = []
        for turn in history[-5:]:
            if not isinstance(turn, dict):
                continue
            who = "User" if turn.get("role") == "user" else "PA"
            text = str(turn.get("content") or "").strip()
            if text:
                lines.append(f"{who}: {text[:500]}")
        if lines:
            parts.append("Conversation so far:\n" + "\n".join(lines))
    focus_block = _focus_block(focus)
    if focus_block:
        parts.append(focus_block)
    if app_context:
        parts.append(app_context)
    # The skill's rules: what must never happen, and how to behave.
    try:
        from pa.services.skills import skill_text

        skill_rules = skill_text(_selected_skills(state), "rules")
    except Exception:
        skill_rules = ""
    if skill_rules:
        parts.append("How to work in these apps:\n" + skill_rules)

    # The request and the rules are the last things to survive a trim: the
    # request is what the answer is about, and the rules are the boundary.
    tail_parts = [f"User request: {request}"]
    if context:
        tail_parts.append(f"User preferences:\n{context}")
    if task_items:
        tail_parts.append("Tasks:\n" + "\n".join(f"- {x}" for x in task_items))
    tail_parts.append(rules)

    head = "\n\n".join(parts)
    tail = "\n\n".join(tail_parts)
    room = MAX_PROMPT_CHARS - len(tail) - 8
    return f"{_fit(head, max(1000, room))}\n\n{tail}"


def enrich_artifacts(state: dict, config: RunnableConfig | None = None) -> dict:
    """Resolve raw ids on cards into human names.

    A card built straight from the API reads "Status 1" because HubSpot
    refers to pipeline stages by id. The names come from a second read, so
    this runs after the main tool and before the summary is written.
    """
    s = _as_dict(state)
    cards = s.get("artifacts") or []
    if not cards:
        return {}
    try:
        from pa.services.app_enrichment import enrich_cards

        enrich_cards(s.get("user_id", ""), cards)
    except Exception:
        # Enrichment is a nicety: a failure must not lose the cards.
        return {}
    return {"artifacts": cards}


def _app_name(slug: str) -> str:
    try:
        from pa.services.app_context import app_display_name

        return app_display_name(slug)
    except Exception:
        return (slug or "").replace("-", " ").title()


def _describe_model_error(exc: Exception) -> str:
    """A model failure, with its status code kept for retry decisions."""
    try:
        from pa.models.errors import describe

        return describe(exc)
    except Exception:
        return f"model_error: {type(exc).__name__}"


def _invoke(model, prompt: str, max_tokens: int | None = None) -> str:
    result = None
    if max_tokens is not None:
        try:
            result = model.invoke(prompt, max_tokens=max_tokens)
        except TypeError:
            # A model double (or a client without the kwarg) — fall back.
            result = None
    if result is None:
        result = model.invoke(prompt) if hasattr(model, "invoke") else model(prompt)
    text = getattr(result, "content", result)
    return text if isinstance(text, str) else str(text)


def _guarded_response(model, prompt: str, state: dict) -> str:
    """Call the model and make sure it only talks about connected apps.

    A prompt rule is not a boundary: asked about "tickets" with no context,
    a model will name a famous ticketing app it was never connected to, and
    the user is then told about data that does not exist. So the answer is
    checked against the real catalog, and corrected if it strays.
    """
    try:
        from pa.services.app_context import guard_app_mentions
    except Exception:
        return _invoke(model, prompt)

    connected = list(state.get("connected_apps") or [])
    text = _invoke(model, prompt)
    try:
        violations = guard_app_mentions(text, connected)
    except Exception:
        violations = []
    if not violations:
        return text

    # One correction, naming the apps that must not be used.
    names = ", ".join(_app_name(v) for v in violations)
    allowed = ", ".join(_app_name(a) for a in connected) or "the connected apps"
    correction = (
        f"\n\nCORRECTION: your answer referred to {names}, which the user has NOT "
        f"connected. Do not describe, summarise, or claim to have used {names}. "
        f"Answer only about {allowed}. If {names} is what the user needs, say "
        f"only that they must connect it."
    )
    try:
        retry = _invoke(model, prompt + correction)
    except Exception:
        return text
    try:
        if guard_app_mentions(retry, connected):
            # Still straying: do not ship an answer that invents app data.
            return (
                f"I can only work with your connected apps ({allowed}), and I could "
                f"not answer that reliably without inventing data. Try asking about "
                f"one of them directly."
            )
    except Exception:
        return text
    return retry


def generate_response(state: dict, config: RunnableConfig | None = None) -> dict:
    s = _as_dict(state)
    if s.get("error"):
        return {}
    try:
        model = _resolve_model(config)
    except Exception as exc:
        return {"error": f"model_error: {type(exc).__name__}: {str(exc)[:500]}"}

    # The user asked for an app they have not connected, and no connected app
    # can answer for it. Say so plainly rather than inventing data.
    unconnected = s.get("unconnected_app") or ""
    target = s.get("target_app") or ""
    if unconnected and not target:
        name = _app_name(unconnected)
        return {
            "response": (
                f"You're not connected to {name} yet, so I can't see any of its data. "
                f"Open the Apps section to connect {name}, then ask me again and I'll "
                f"pick up where we left off."
            )
        }

    try:
        prompt = _prompt_for(s)
        # Support both LangChain chat models (invoke -> AIMessage) and fakes.
        text = _guarded_response(model, prompt, s)
    except Exception as exc:  # bounded: surfaced in state, retried only for transient (see tasks)
        return {"error": _describe_model_error(exc)}
    text = text.strip()
    if not text:
        return {"error": "empty model response"}
    return {"response": text}



TOOL_DECISION_FORMAT = (
    "Reply with ONE JSON object and nothing else. Exactly one of:\n"
    'A) tool call: {"action":"tool","tool":"<EXACT_TOOL_SLUG>","args":{...}}\n'
    'B) plain reply: {"action":"reply","text":"..."}\n'
    "Use A only when a listed tool can answer the request. Never invent a tool slug."
)

# A provider's token budget is per minute and shared across requests, not
# per request: one oversized prompt can starve the next one. Groq's
# on-demand tier allows 8k tokens per minute, and a single assistant turn
# makes three model calls, so every prompt is held well under a third of it.
MAX_PROMPT_CHARS = 7000
MAX_TOOL_LIST_CHARS = 4000
MAX_TOOL_DESCRIPTION_CHARS = 70
MAX_TOOLS_SHOWN = 120

# The tool-decision answer is one small JSON object; it does not need the
# default completion budget, and completions count towards the same limit.
TOOL_DECISION_MAX_TOKENS = 500


def _fit(text: str, limit: int) -> str:
    """Trim a block to a size, marking that it was cut."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 20)].rstrip() + "\n[trimmed]"


def _token_forms(token: str) -> list[str]:
    """The word and its singular, so "tickets" matches "TICKET".

    Users write plurals; tool slugs are not consistent about it. Without
    this, the obvious tool ("LIST_TICKETS" for "show my tickets") does not
    match and a less relevant one is shown instead.
    """
    forms = [token]
    if token.endswith("ies") and len(token) > 4:
        forms.append(token[:-3] + "y")
    elif token.endswith("es") and len(token) > 4:
        forms.append(token[:-2])
    elif token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        forms.append(token[:-1])
    return forms


def _rank_tools(slug: str, meta: dict, tokens: list[str]) -> int:
    """How relevant a tool is to the request, for ordering the list.

    The slug carries the entity and the verb, so a match there counts for
    much more than a match in the prose description.
    """
    if not tokens:
        return 0
    slug_low = slug.lower()
    desc_low = str(meta.get("description", "")).lower()
    score = 0
    for tok in tokens:
        forms = _token_forms(tok)
        if any(form in slug_low for form in forms):
            score += 5
        elif any(form in desc_low for form in forms):
            score += 1
    return score


def _tool_list(scoped: dict, tokens: list[str], limit: int, budget: int = MAX_TOOL_LIST_CHARS) -> str:
    """The tool list, ranked by relevance and bounded by size."""
    ranked = sorted(
        scoped.items(),
        key=lambda item: (-_rank_tools(item[0], item[1], tokens), item[0]),
    )
    lines: list[str] = []
    used = 0
    for slug, meta in ranked:
        if len(lines) >= limit:
            break
        line = f"- {slug} ({meta['app']}): {meta['description'][:MAX_TOOL_DESCRIPTION_CHARS]}"
        if used + len(line) > budget:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


def _target_app(state: dict) -> str | None:
    """The connected app this request is about, if it can be identified."""
    try:
        from pa.services.app_context import detect_target_app

        return detect_target_app(
            state.get("request", ""),
            state.get("history") or [],
            state.get("connected_apps") or [],
        )
    except Exception:
        return None


def plan_action(state: dict, config: RunnableConfig | None = None) -> dict:
    """Let the assistant pick a real tool, or answer in prose.

    The request is pinned to one app when that can be identified, and only
    that app's tools are offered or accepted. Without this, a request like
    "tickets" is matched against every connected app's ticket tools and the
    wrong app wins — and because the model may name a tool it was never
    shown, a tool from another app could be accepted and run.
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

    # The scope was resolved in prepare_context; fall back to detecting it
    # here when the node is called on its own.
    target_app = s.get("target_app") or _target_app(s)

    # The user asked for an app they have not connected, and no connected app
    # can answer for it. Run nothing: the answer is "connect it first", and
    # running some other app's tool would be inventing data.
    unconnected = s.get("unconnected_app") or ""
    if unconnected and not target_app:
        return {"approval_status": "not_required"}

    if target_app:
        scoped = {slug: meta for slug, meta in index.items() if meta["app"] == target_app}
        if not scoped:
            # The app is connected but exposes no tools: do not fall back to
            # other apps' tools.
            return {"approval_status": "not_required"}
    else:
        scoped = index

    request = s.get("request", "")
    tokens = [t for t in request.lower().split() if len(t) >= 3]

    if target_app:
        scope_note = (
            f"The user is working in the {target_app} app. Use only {target_app} "
            f"tools. Never use another app's tool for this request.\n\n"
        )
    else:
        scope_note = (
            "If more than one app could serve the request, prefer the one the "
            "user named, or the one the conversation is about.\n\n"
        )

    focus = s.get("focus") or {}
    focus_block = _focus_block(focus)
    focus_note = f"{focus_block}\n\n" if focus_block else ""

    # The skill's guide: which tools this app exposes, and the order to use
    # them in. This is the part that stops the model guessing a slug.
    try:
        from pa.services.skills import skill_text

        guide = skill_text(_selected_skills(s), "guide")
    except Exception:
        guide = ""

    # The prompt is assembled to a fixed size: the guide and the tool list
    # share what is left, and the request itself is never trimmed.
    head = f"{TOOL_DECISION_FORMAT}\n\n{scope_note}{focus_note}"
    tail = f"\n\nUser request: {request}"
    overhead = len(head) + len(tail) + 80
    room = max(1000, MAX_PROMPT_CHARS - overhead)

    guide = _fit(guide, int(room * 0.35))
    list_budget = max(1000, room - len(guide))
    available = _tool_list(scoped, tokens, MAX_TOOLS_SHOWN, list_budget)

    prompt = f"{head}{guide}\n\nConnected app tools you may call:\n{available}{tail}"
    try:
        model = _resolve_model(config)
        raw = _invoke(model, prompt, max_tokens=TOOL_DECISION_MAX_TOKENS)
        text = getattr(raw, "content", raw)
        text = text if isinstance(text, str) else str(text)
    except Exception as exc:
        # Keep the status code: without it a rate limit is indistinguishable
        # from a bad request, and neither can be retried.
        return {"error": _describe_model_error(exc)}

    call = parse_tool_call(text)
    if not call:
        return {"approval_status": "not_required"}
    # Validated against the *scoped* set: a tool from an app the user did
    # not ask about is rejected even if it is connected and callable.
    if call["tool"] not in scoped:
        return {"approval_status": "not_required"}
    call["classification"] = scoped[call["tool"]]["classification"]
    call["app"] = scoped[call["tool"]]["app"]
    return {"tool_call": call}


def run_read_tool(state: dict, config: RunnableConfig | None = None) -> dict:
    """Execute a read-only tool immediately.

    The raw payload is also normalised into *artifacts* — structured cards
    (a HubSpot engagement, a contact, ...) that the chat renders and the
    user can act on. Prose alone would lose the record.
    """
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

    # Pop the raw payload so only the (bounded) text enters graph state.
    payload = result.pop("payload", None)
    artifacts = _build_artifacts(call.get("tool", ""), call.get("app", ""), payload)
    if artifacts:
        return {"tool_result": result, "artifacts": artifacts}
    return {"tool_result": result}


def _build_artifacts(tool_slug: str, app: str, payload: Any) -> list[dict]:
    """Normalise a tool payload into cards. Never raises."""
    try:
        from pa.services.artifacts import build_cards

        return build_cards(tool_slug, app, payload)
    except Exception:
        return []


def summarize_tool_result(state: dict, config: RunnableConfig | None = None) -> dict:
    """Turn a raw tool payload into a short human answer."""
    s = _as_dict(state)
    result = s.get("tool_result") or {}
    if s.get("error") or not result:
        return {}
    if result.get("error"):
        return {"response": f"That didn't work: {result['error']}"}
    raw = result.get("result", "")
    # The raw payload is trimmed to fit the request budget; a summary does
    # not need every field of every record.
    raw = _fit(raw, MAX_PROMPT_CHARS // 2)
    try:
        model = _resolve_model(config)
        prompt = (
            f"User asked: {s.get('request', '')}\n\n"
            f"Tool `{result.get('tool')}` returned:\n{raw}\n\n"
            "Summarise the answer in a few short lines. Mention concrete names, "
            "counts and values from the data. Do not speculate beyond it. "
            "Do not mention any app the user has not connected."
        )
        text = _guarded_response(model, prompt, s)
    except Exception as exc:
        # A failed summary is an error, not an answer: the run can be retried,
        # and the records are still shown as cards.
        return {"error": _describe_model_error(exc)}
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
        tool = call.get("tool")
        app = call.get("app")
        focus = s.get("focus") or {}
        summary = f"Run {tool} in {app}"
        record_id = ""
        if isinstance(focus, dict) and focus:
            label = focus.get("type") or focus.get("kind") or "record"
            title = focus.get("title") or ""
            record_id = str(focus.get("record_id") or "")
            target = f"{label} '{title}'" if title else str(label)
            if record_id:
                target += f" (ID {record_id})"
            summary = f"{target} — {summary}"
        return {
            "proposed_action": {
                "action": "composio_tool",
                "tool": tool,
                "app": app,
                "args": call.get("args") or {},
                "summary": summary,
                **({"record_id": record_id} if record_id else {}),
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
