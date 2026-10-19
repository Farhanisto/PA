"""Unit tests: app records normalised into assistant cards."""

import pytest

from pa.services.artifacts import build_cards


HUBSPOT_ENGAGEMENTS = {
    "results": [
        {
            "id": 529351971048,
            "type": "EMAIL",
            "timestamp": 1792190749385,
            "active": True,
            "associations": {"contactIds": [895130991857]},
            "metadata": {
                "body": "Hey Brian, we're the number-one cupcake supplier.",
                "subject": "(Sample email) Hello there",
                "fromEmail": "sam@cupcakes.example",
                "toEmail": "bh@hubspot.com",
            },
        },
        {
            "id": 529351971046,
            "type": "CALL",
            "timestamp": 1792363549385,
            "metadata": {"body": "(Sample call) ...extra tasty cupcake option..."},
        },
        {
            "id": 529351971047,
            "type": "MEETING",
            "timestamp": 1792622749385,
            "metadata": {
                "title": "(Sample meeting) Cupcake creator demo",
                "attendees": "bh@hubspot.com",
                "startTime": "1792622749385",
                "endTime": "1792626349385",
            },
        },
    ]
}


def test_hubspot_engagement_becomes_a_card():
    cards = build_cards("HUBSPOT_GET_ENGAGEMENTS", "hubspot", HUBSPOT_ENGAGEMENTS)

    assert len(cards) == 3
    email = cards[0]
    assert email["kind"] == "engagement"
    assert email["app"] == "hubspot"
    assert email["type"] == "Email"
    assert email["title"] == "(Sample email) Hello there"
    assert "cupcake supplier" in email["body"]
    assert email["meta"]


def test_hubspot_engagement_exposes_reply_action():
    cards = build_cards("HUBSPOT_GET_ENGAGEMENTS", "hubspot", HUBSPOT_ENGAGEMENTS)
    email = cards[0]

    reply = next(a for a in email["actions"] if a["kind"] == "reply")
    assert reply["recipient"] == "sam@cupcakes.example"
    assert reply["subject"] == "(Sample email) Hello there"


def test_hubspot_engagement_types_are_labelled():
    cards = build_cards("HUBSPOT_GET_ENGAGEMENTS", "hubspot", HUBSPOT_ENGAGEMENTS)
    assert [c["type"] for c in cards] == ["Email", "Call", "Meeting"]


def test_hubspot_engagement_fields_include_id_and_date():
    cards = build_cards("HUBSPOT_GET_ENGAGEMENTS", "hubspot", HUBSPOT_ENGAGEMENTS)
    labels = {f["label"]: f["value"] for f in cards[0]["fields"]}
    assert labels["Engagement ID"] == "529351971048"
    assert "Engagement ID" in labels
    assert "Created" in labels


def test_hubspot_ticket_becomes_a_card():
    payload = {
        "results": [
            {
                "id": 439347004625,
                "createdAt": "2026-10-18T22:51:28.000Z",
                "properties": {
                    "subject": "TEST Feature request",
                    "content": "I would like a dark mode.",
                    "hs_pipeline_stage": "1",
                    "hs_pipeline": "0",
                    "hs_ticket_priority": "LOW",
                    "hs_ticket_category": "PROBLEM",
                    "createdate": "2026-10-18T22:51:28.000Z",
                    "hs_lastmodifieddate": "2026-10-18T22:51:28.000Z",
                },
            }
        ]
    }
    cards = build_cards("HUBSPOT_LIST_TICKETS", "hubspot", payload)

    assert len(cards) == 1
    card = cards[0]
    assert card["kind"] == "ticket"
    assert card["title"] == "TEST Feature request"
    assert "dark mode" in card["body"]
    labels = {f["label"]: f["value"] for f in card["fields"]}
    assert labels["Priority"] == "LOW"
    assert labels["Status"] == "1"
    assert labels["Ticket ID"] == "439347004625"
    assert "2026" in labels["Created"]
    assert card["meta"]


def test_a_ticket_carries_priority_as_a_badge():
    payload = {
        "results": [
            {"id": "1", "properties": {"subject": "S", "hs_ticket_priority": "HIGH"}},
            {"id": "2", "properties": {"subject": "S", "hs_ticket_priority": "LOW"}},
        ]
    }
    cards = build_cards("HUBSPOT_LIST_TICKETS", "hubspot", payload)
    assert cards[0]["badges"][0]["value"] == "HIGH"
    assert cards[0]["badges"][0]["tone"] == "danger"
    assert cards[1]["badges"][0]["tone"] == "neutral"


def test_a_ticket_is_interactive():
    payload = {
        "results": [
            {"id": "1", "properties": {"subject": "Login issue", "content": "Cannot log in."}}
        ]
    }
    cards = build_cards("HUBSPOT_LIST_TICKETS", "hubspot", payload)
    assert cards[0]["actions"]
    assert any(a["kind"] == "ask" for a in cards[0]["actions"])


@pytest.mark.parametrize(
    "payload",
    [
        {"results": [{"id": "1", "subject": "S"}]},
        {"data": {"results": [{"id": "1", "subject": "S"}]}},
        {"successful": True, "data": {"results": [{"id": "1", "subject": "S"}]}},
        {"items": [{"id": "1", "subject": "S"}]},
        {"tickets": [{"id": "1", "subject": "S"}]},
        [{"id": "1", "subject": "S"}],
    ],
)
def test_records_are_found_in_any_envelope(payload):
    """Apps wrap results differently; the records must still be found."""
    cards = build_cards("HUBSPOT_LIST_TICKETS", "hubspot", payload)
    assert len(cards) == 1, payload
    assert cards[0]["title"] == "S"


def test_paging_metadata_is_not_mistaken_for_records():
    payload = {
        "results": [{"id": "1", "subject": "S"}],
        "paging": {"next": {"after": "123", "link": "https://api.example"}},
    }
    cards = build_cards("HUBSPOT_LIST_TICKETS", "hubspot", payload)
    assert len(cards) == 1


def test_hubspot_contact_becomes_a_card():
    payload = {
        "results": [
            {
                "id": 895130991857,
                "properties": {
                    "firstname": "Brian",
                    "lastname": "Halligan",
                    "email": "bh@hubspot.com",
                    "company": "HubSpot",
                    "lifecyclestage": "customer",
                },
            }
        ]
    }
    cards = build_cards("HUBSPOT_GET_CONTACT_BY_ID", "hubspot", payload)

    assert len(cards) == 1
    card = cards[0]
    assert card["kind"] == "contact"
    assert card["title"] == "Brian Halligan"
    assert card["subtitle"] == "bh@hubspot.com"
    assert any(a["kind"] == "reply" for a in card["actions"])


def test_hubspot_deal_becomes_a_card():
    payload = {
        "results": [
            {
                "id": "deal-1",
                "properties": {
                    "dealname": "Cupcake expansion",
                    "amount": "12000",
                    "dealstage": "qualifiedtobuy",
                },
            }
        ]
    }
    cards = build_cards("HUBSPOT_LIST_DEALS", "hubspot", payload)

    assert cards[0]["kind"] == "deal"
    assert cards[0]["title"] == "Cupcake expansion"
    labels = {f["label"]: f["value"] for f in cards[0]["fields"]}
    assert labels["Amount"] == "12000"
    assert labels["Stage"] == "qualifiedtobuy"


def test_unknown_app_falls_back_to_a_generic_card():
    payload = {"results": [{"id": "s-1", "name": "Deploy finished", "status": "done"}]}
    cards = build_cards("SLACK_LIST_MESSAGES", "slack", payload)

    assert len(cards) == 1
    card = cards[0]
    assert card["app"] == "slack"
    assert card["kind"] == "message"
    assert card["title"] == "Deploy finished"


def test_generic_card_offers_an_ask_action():
    payload = {"results": [{"id": "s-1", "name": "Deploy finished"}]}
    cards = build_cards("SLACK_LIST_MESSAGES", "slack", payload)
    assert any(a["kind"] == "ask" for a in cards[0]["actions"])


def test_payload_shapes_are_all_accepted():
    record = {"id": "1", "name": "A thing"}
    for payload in (
        [record],
        {"results": [record]},
        {"data": [record]},
        {"items": [record]},
        {"data": {"results": [record]}},
    ):
        cards = build_cards("ACME_LIST_THINGS", "acme", payload)
        assert len(cards) == 1, payload


def test_json_string_payload_is_parsed():
    import json

    cards = build_cards(
        "HUBSPOT_LIST_DEALS", "hubspot", json.dumps(HUBSPOT_ENGAGEMENTS)
    )
    assert len(cards) == 3


@pytest.mark.parametrize("payload", [None, "", "not json", {}, [], 42, {"results": "nope"}])
def test_malformed_payload_yields_no_cards_and_never_raises(payload):
    assert build_cards("HUBSPOT_GET_ENGAGEMENTS", "hubspot", payload) == []


def test_card_count_is_capped():
    payload = {"results": [{"id": str(i), "name": f"Thing {i}"} for i in range(100)]}
    cards = build_cards("ACME_LIST_THINGS", "acme", payload)
    assert len(cards) <= 25


def test_long_body_is_truncated():
    payload = {"results": [{"id": "1", "name": "Long", "body": "x" * 9000}]}
    cards = build_cards("ACME_LIST_THINGS", "acme", payload)
    assert len(cards[0]["body"]) <= 1200


def test_actions_are_never_empty_for_a_readable_record():
    payload = {"results": [{"id": "1", "name": "A thing"}]}
    cards = build_cards("ACME_LIST_THINGS", "acme", payload)
    assert cards[0]["actions"]


def test_millisecond_and_second_epochs_both_render():
    from pa.services.artifacts import _fmt_time

    ms = _fmt_time(1792190749385)
    secs = _fmt_time(1792190749)
    assert ms and secs
    assert ms[:4] == secs[:4]


def test_iso_timestamp_renders():
    from pa.services.artifacts import _fmt_time

    assert _fmt_time("2026-09-30T14:45:49.385Z")


def test_bad_timestamp_is_blank_not_an_error():
    from pa.services.artifacts import _fmt_time

    assert _fmt_time("not a date") == ""
    assert _fmt_time(None) == ""
