"""Unit tests: resolving raw ids on cards into human names."""

import pytest


def _ticket(stage="1", pipeline="0", priority="HIGH"):
    return {
        "id": "hubspot:ticket:439347004625",
        "app": "hubspot",
        "kind": "ticket",
        "type": "Ticket",
        "title": "TEST Feature request",
        "subtitle": "PROBLEM",
        "body": "I would like a dark mode.",
        "fields": [
            {"label": "Status", "value": stage},
            {"label": "Priority", "value": priority},
            {"label": "Pipeline", "value": pipeline},
            {"label": "Ticket ID", "value": "439347004625"},
        ],
        "badges": [
            {"label": "Priority", "value": priority, "tone": "danger"},
            {"label": "Stage", "value": stage, "tone": "neutral"},
        ],
        "meta": "18 Oct 2026, 22:51",
        "actions": [],
    }


# --- parsing ---------------------------------------------------------------


def test_stage_names_come_from_a_flat_stages_payload():
    from pa.services.app_enrichment import _stage_pairs

    payload = {"results": [{"id": "1", "label": "New"}, {"id": "4", "label": "Closed"}]}
    assert _stage_pairs(payload) == {"1": "New", "4": "Closed"}


def test_stage_names_come_from_nested_stages_in_a_pipelines_payload():
    from pa.services.app_enrichment import _stage_pairs

    payload = {
        "results": [
            {
                "id": "0",
                "label": "Sales Pipeline",
                "stages": [{"id": "1", "label": "New"}, {"id": "2", "label": "Working"}],
            }
        ]
    }
    assert _stage_pairs(payload) == {"1": "New", "2": "Working"}


def test_pipeline_names_come_from_the_pipeline_itself():
    from pa.services.app_enrichment import _flat_pairs

    payload = {"results": [{"id": "0", "label": "Sales Pipeline"}]}
    assert _flat_pairs(payload) == {"0": "Sales Pipeline"}


# --- application -----------------------------------------------------------


@pytest.fixture()
def fake_lookup(monkeypatch):
    """Stand in for the Composio lookup."""
    from pa.services import app_enrichment

    calls: list[tuple[str, dict]] = []

    def _lookup(user_id, spec, pipeline_ids, object_type=""):
        calls.append((spec.kind, tuple(pipeline_ids), object_type))
        if spec.kind == "stage":
            return {"1": "New", "2": "Working", "4": "Closed"}
        return {"0": "Sales Pipeline"}

    monkeypatch.setattr(app_enrichment, "_lookup", _lookup)
    return calls


def test_a_stage_id_becomes_its_name(fake_lookup):
    from pa.services.app_enrichment import enrich_cards

    card = _ticket()
    enrich_cards("u1", [card])

    fields = {f["label"]: f["value"] for f in card["fields"]}
    assert fields["Status"] == "New"
    assert fields["Pipeline"] == "Sales Pipeline"
    badges = {b["label"]: b["value"] for b in card["badges"]}
    assert badges["Stage"] == "New"
    assert badges["Priority"] == "HIGH"


def test_an_unknown_id_is_left_alone(fake_lookup):
    from pa.services.app_enrichment import enrich_cards

    card = _ticket(stage="99")
    enrich_cards("u1", [card])
    fields = {f["label"]: f["value"] for f in card["fields"]}
    assert fields["Status"] == "99"


def test_cards_for_apps_without_enrichment_are_untouched(fake_lookup):
    from pa.services.app_enrichment import enrich_cards

    card = _ticket()
    card["app"] = "slack"
    enrich_cards("u1", [card])
    fields = {f["label"]: f["value"] for f in card["fields"]}
    assert fields["Status"] == "1"


def test_a_failed_lookup_leaves_the_card_usable(monkeypatch):
    from pa.services import app_enrichment

    def _boom(user_id, spec, pipeline_ids, object_type=""):
        raise RuntimeError("composio down")

    monkeypatch.setattr(app_enrichment, "_lookup", _boom)
    card = _ticket()
    app_enrichment.enrich_cards("u1", [card])
    fields = {f["label"]: f["value"] for f in card["fields"]}
    assert fields["Status"] == "1"


def test_no_user_means_no_lookup(monkeypatch):
    from pa.services import app_enrichment

    def _boom(*_args):
        raise AssertionError("should not look anything up")

    monkeypatch.setattr(app_enrichment, "_lookup", _boom)
    card = _ticket()
    app_enrichment.enrich_cards("", [card])
    assert card["fields"][0]["value"] == "1"


def test_the_pipeline_id_is_offered_to_the_lookup(monkeypatch):
    from pa.services import app_enrichment

    seen: list[dict] = []

    def _lookup(user_id, spec, pipeline_ids, object_type=""):
        seen.append({"kind": spec.kind, "pipelines": list(pipeline_ids)})
        return {}

    monkeypatch.setattr(app_enrichment, "_lookup", _lookup)
    app_enrichment.enrich_cards("u1", [_ticket(pipeline="7")])

    stage_call = next(c for c in seen if c["kind"] == "stage")
    assert stage_call["pipelines"] == ["7"]


# --- the real lookup path --------------------------------------------------


def test_the_lookup_refuses_a_write_tool(monkeypatch):
    """Enrichment must never mutate anything."""
    from pa.services import tool_runner

    index = {
        "HUBSPOT_GET_PIPELINE_STAGES": {
            "app": "hubspot",
            "description": "",
            "required": [],
            "properties": {},
            "classification": "read",
        },
        "HUBSPOT_UPDATE_TICKET": {
            "app": "hubspot",
            "description": "",
            "required": [],
            "properties": {},
            "classification": "write",
        },
    }
    monkeypatch.setattr(tool_runner, "tool_index", lambda user_id: dict(index))

    with pytest.raises(tool_runner.ToolError):
        tool_runner.execute_read("u1", "HUBSPOT_UPDATE_TICKET", {})


def test_the_lookup_refuses_an_unknown_tool(monkeypatch):
    from pa.services import tool_runner

    monkeypatch.setattr(tool_runner, "tool_index", lambda user_id: {})
    with pytest.raises(tool_runner.ToolError):
        tool_runner.execute_read("u1", "HUBSPOT_GET_PIPELINE_STAGES", {})


# --- briefing sources ------------------------------------------------------


def test_the_hubspot_briefing_source_discovers_its_list_tools():
    """A renamed slug must still resolve, via the pattern fallback."""
    from pa.services.briefing import SOURCES, _resolve_tool

    index = {
        "HUBSPOT_LIST_TICKETS": {"app": "hubspot", "classification": "read"},
        "HUBSPOT_RETRIEVE_PIPELINE_STAGE_BY_ID": {
            "app": "hubspot",
            "classification": "read",
        },
        "HUBSPOT_CREATE_TICKET": {"app": "hubspot", "classification": "write"},
    }
    source = next(s for s in SOURCES["hubspot"] if s.group == "Tickets")
    resolved = _resolve_tool(index, source)
    assert resolved is not None
    slug, _args = resolved
    assert slug == "HUBSPOT_LIST_TICKETS"


def test_a_briefing_source_never_picks_a_write_tool():
    from pa.services.briefing import SOURCES, _resolve_tool

    index = {
        "HUBSPOT_CREATE_TICKET": {"app": "hubspot", "classification": "write"},
    }
    source = next(s for s in SOURCES["hubspot"] if s.group == "Tickets")
    assert _resolve_tool(index, source) is None


def test_an_app_with_no_sources_contributes_nothing():
    from pa.services.briefing import build_app_cards

    cards, sources = build_app_cards("u1", "notion", index={})
    assert cards == []
    assert sources == []
