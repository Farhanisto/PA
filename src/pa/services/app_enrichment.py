"""Resolving raw ids on cards into human names.

HubSpot records refer to pipeline stages and pipelines by id — a card built
straight from the API reads "Status 1" instead of "New" and "Pipeline 0"
instead of "Sales Pipeline". The names live behind a second call
(``HUBSPOT_GET_PIPELINE_STAGES``), so cards are enriched after the main
read and before the user sees them.

The lookup is a read tool, so it runs under the same rules as everything
else: only for apps the user has connected, and never a write.

Enrichment is best-effort. If the lookup fails, is unavailable, or the id
is unknown, the card keeps its raw value — a card with a raw id is far
better than no card at all.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from pa.services.artifacts import _first, _records, _text

logger = logging.getLogger(__name__)

# Names change rarely; a short cache keeps a burst of questions cheap.
_LOOKUP_TTL_SECONDS = 300.0
_lookup_cache: dict[str, tuple[float, dict[str, str]]] = {}


@dataclass(frozen=True)
class Enrichment:
    """One lookup, and where its results apply on a card.

    ``tools`` are candidate ``(slug, args)`` pairs tried in order. The
    literal ``"$pipeline"`` in an argument is replaced with the pipeline id
    the cards themselves refer to.

    ``patterns`` are a fallback: tool slugs change between SDK versions
    (``HUBSPOT_GET_PIPELINE_STAGES`` became
    ``HUBSPOT_RETRIEVE_PIPELINE_STAGES``), so a hardcoded slug silently
    stops resolving. A pattern is matched against the user's real tool
    index instead.
    """

    kind: str
    tools: tuple[tuple[str, dict[str, Any]], ...]
    fields: tuple[str, ...] = ()
    badges: tuple[str, ...] = ()
    parser: str = "flat"
    patterns: tuple[str, ...] = ()


# Apps with bespoke enrichment. Each app plugs in here; the mechanism is
# generic, so a new app needs a lookup spec and nothing else.
ENRICHMENTS: dict[str, list[Enrichment]] = {
    "hubspot": [
        Enrichment(
            kind="stage",
            tools=(
                (
                    "HUBSPOT_RETRIEVE_PIPELINE_STAGES",
                    {"objectType": "$object_type", "pipelineId": "$pipeline"},
                ),
            ),
            patterns=("*PIPELINE_STAGE*",),
            fields=("Status",),
            badges=("Stage",),
            parser="stage",
        ),
        Enrichment(
            kind="pipeline",
            tools=(
                (
                    "HUBSPOT_RETRIEVE_ALL_PIPELINES_FOR_SPECIFIED_OBJECT_TYPE",
                    {"objectType": "$object_type"},
                ),
            ),
            patterns=("*ALL_PIPELINES*",),
            fields=("Pipeline",),
            parser="flat",
        ),
    ],
}

# Irregular plurals for the CRM object types.
_IRREGULAR = {
    "company": "companies",
    "person": "people",
    "activity": "activities",
    "deal": "deals",
}


def _object_type(kind: str) -> str:
    """The CRM object type for a card kind ("ticket" -> "tickets")."""
    kind = (kind or "").lower()
    if kind in _IRREGULAR:
        return _IRREGULAR[kind]
    if kind.endswith("y") and len(kind) > 2:
        return kind[:-1] + "ies"
    if kind.endswith(("s", "x", "ch", "sh")):
        return kind + "es"
    return kind + "s" if kind else ""


def _is_error_payload(payload: Any) -> bool:
    """Did the app return an error instead of records?

    Some tools return ``{"message": ..., "status_code": 400}`` as a normal
    payload rather than raising, so a bad request would otherwise look like
    an empty (or meaningless) result.
    """
    if not isinstance(payload, dict):
        return False
    status = payload.get("status_code")
    if isinstance(status, int) and status >= 400:
        return True
    if "message" in payload and "results" not in payload and "data" not in payload:
        return True
    return False


# --- parsing ---------------------------------------------------------------


def _stage_pairs(payload: Any) -> dict[str, str]:
    """{stage id: stage name} from a stages or pipelines payload.

    A pipelines response nests its stages, and those names are the ones that
    matter; the pipeline names themselves are only used when there are no
    nested stages.
    """
    flat: dict[str, str] = {}
    nested: dict[str, str] = {}
    for rec in _records(payload):
        rid = _text(_first(rec, "id", "stageId", "stage_id"), 64)
        label = _text(_first(rec, "label", "name", "displayName", "title"), 120)
        if rid and label:
            flat[rid] = label
        stages = rec.get("stages")
        for stage in stages if isinstance(stages, list) else []:
            if not isinstance(stage, dict):
                continue
            sid = _text(_first(stage, "id", "stageId", "stage_id"), 64)
            slabel = _text(_first(stage, "label", "name", "displayName"), 120)
            if sid and slabel:
                nested[sid] = slabel
    return nested or flat


def _flat_pairs(payload: Any) -> dict[str, str]:
    """{id: name} from a flat list of records."""
    out: dict[str, str] = {}
    for rec in _records(payload):
        rid = _text(_first(rec, "id", "pipelineId", "pipeline_id", "stageId"), 64)
        label = _text(_first(rec, "label", "name", "displayName", "title"), 120)
        if rid and label:
            out[rid] = label
    return out


_PARSERS = {"stage": _stage_pairs, "flat": _flat_pairs}


# --- lookup ----------------------------------------------------------------


def _cache_key(user_id: str, slug: str, args: dict) -> str:
    return f"{user_id}|{slug}|{json.dumps(args, sort_keys=True, default=str)}"


def _pattern_score(slug: str) -> int:
    """How likely a matched slug is to be the "list them all" tool.

    A plural slug lists; one ending in ``BY_ID`` needs a record id, which
    the enrichment does not have.
    """
    score = 0
    if slug.endswith("S"):
        score += 3
    if "BY_ID" in slug or "_BY_" in slug:
        score -= 5
    if any(v in slug for v in ("LIST", "GET_ALL", "RETRIEVE_ALL", "RETRIEVE", "SEARCH", "GET")):
        score += 1
    return score


def _pattern_candidates(index: dict, patterns: tuple[str, ...]) -> list[str]:
    """Tool slugs matching any pattern, best first."""
    from fnmatch import fnmatch

    found: dict[str, int] = {}
    for pattern in patterns:
        for slug, meta in index.items():
            if meta.get("classification") != "read":
                continue
            if fnmatch(slug, pattern):
                found[slug] = max(found.get(slug, -99), _pattern_score(slug))
    return [slug for slug, _ in sorted(found.items(), key=lambda kv: (-kv[1], kv[0]))]


def _resolve_args(args: dict, pipeline_ids: list[str], object_type: str) -> dict:
    """Fill the placeholders in a tool's arguments from the cards."""
    resolved: dict[str, Any] = {}
    for key, value in args.items():
        if value == "$pipeline":
            resolved[key] = pipeline_ids[0] if pipeline_ids else "0"
        elif value == "$object_type":
            resolved[key] = object_type
        else:
            resolved[key] = value
    return resolved


def _lookup(
    user_id: str,
    spec: Enrichment,
    pipeline_ids: list[str],
    object_type: str = "",
) -> dict[str, str]:
    """Resolve ids to names for one enrichment. Never raises."""
    from pa.services.tool_runner import ToolError, execute_read, tool_index

    try:
        index = tool_index(user_id)
    except Exception as exc:
        logger.warning("enrichment: tool index unavailable: %s", type(exc).__name__)
        return {}

    candidates: list[tuple[str, dict[str, Any]]] = []
    for slug, args in spec.tools:
        candidates.append((slug, _resolve_args(args, pipeline_ids, object_type)))
        if "$pipeline" in args.values():
            for pid in pipeline_ids:
                extra = {k: (pid if v == "$pipeline" else v) for k, v in args.items()}
                candidates.append((slug, _resolve_args(extra, pipeline_ids, object_type)))

    # The hardcoded slugs are tried first; the patterns cover the case where
    # the toolkit has renamed them.
    if spec.patterns:
        for slug in _pattern_candidates(index, spec.patterns):
            if all(slug != c for c, _ in candidates):
                candidates.append((slug, _resolve_args({}, pipeline_ids, object_type)))

    parser = _PARSERS.get(spec.parser, _flat_pairs)
    for slug, args in candidates:
        if slug not in index:
            continue
        if index[slug].get("classification") != "read":
            continue
        key = _cache_key(user_id, slug, args)
        cached = _lookup_cache.get(key)
        if cached is not None and (time.monotonic() - cached[0]) < _LOOKUP_TTL_SECONDS:
            if cached[1]:
                return cached[1]
            continue
        try:
            payload = execute_read(user_id, slug, args)
        except ToolError:
            continue
        except Exception as exc:
            logger.warning("enrichment lookup %s failed: %s", slug, type(exc).__name__)
            continue
            # The app rejected the request (e.g. a missing argument). Try the
            # next candidate rather than treating this as "no stages".
            logger.info("enrichment lookup %s returned an error payload", slug)
            continue
        pairs = parser(payload)
        _lookup_cache[key] = (time.monotonic(), pairs)
        if pairs:
            return pairs
    return {}


# --- application -----------------------------------------------------------


def _apply(card: dict[str, Any], spec: Enrichment, lookup: dict[str, str]) -> None:
    """Replace raw ids on a card with their names."""
    for field in card.get("fields") or []:
        if field.get("label") in spec.fields:
            name = lookup.get(str(field.get("value") or "").strip())
            if name:
                field["value"] = name
    for badge in card.get("badges") or []:
        if badge.get("label") in spec.badges:
            name = lookup.get(str(badge.get("value") or "").strip())
            if name:
                badge["value"] = name


def enrich_cards(user_id: str, cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve raw ids on cards into human names, where the app needs it.

    Cards are enriched in place and returned. A card whose id cannot be
    resolved keeps its raw value.
    """
    if not cards or not user_id:
        return cards

    by_app: dict[str, list[dict[str, Any]]] = {}
    for card in cards:
        app = (card.get("app") or "").lower()
        by_app.setdefault(app, []).append(card)

    for app, app_cards in by_app.items():
        specs = ENRICHMENTS.get(app)
        if not specs:
            continue
        pipeline_ids = sorted(
            {
                str(f.get("value") or "").strip()
                for c in app_cards
                for f in (c.get("fields") or [])
                if f.get("label") == "Pipeline" and str(f.get("value") or "").strip()
            }
        )
        # The CRM object type the lookup needs ("tickets", "deals", ...).
        kinds = {(c.get("kind") or "").lower() for c in app_cards}
        object_type = ""
        for kind in kinds:
            if kind:
                object_type = _object_type(kind)
                break
        for spec in specs:
            try:
                lookup = _lookup(user_id, spec, pipeline_ids, object_type)
            except Exception as exc:
                logger.warning("enrichment failed for %s: %s", app, type(exc).__name__)
                continue
            if not lookup:
                continue
            for card in app_cards:
                _apply(card, spec, lookup)
    return cards
