"""Offline fulfillment integration; real operational planning, fake model output."""

import json
import socket
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from intent import ParsedIntent, parse_intent
from llm_client import FakeLLMClient, LLMResponse, LLMStatus
from router import execute_intent
from repositories import Repository
import tools
import ui


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Network forbidden")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.mark.parametrize("text", [
    "Can PO-2026-1026 be fulfilled?", "Plan fulfillment for PO-2026-1026",
    "How much of PO-2026-1026 can we fulfill?",
])
def test_parse(text):
    client = FakeLLMClient('{"intent":"PLAN_FULFILLMENT","po_id":"PO-2026-1026"}')
    assert parse_intent(text, client) == ParsedIntent("PLAN_FULFILLMENT", po_id="PO-2026-1026")


def test_route_and_missing(monkeypatch):
    tool = Mock(return_value=object())
    monkeypatch.setattr(tools, "plan_fulfillment", tool)
    result = execute_intent(ParsedIntent("PLAN_FULFILLMENT", po_id="PO-2026-1026"))
    tool.assert_called_once_with(po_id="PO-2026-1026")
    assert result.result is tool.return_value
    assert result.po_id == "PO-2026-1026"
    tool.reset_mock()
    for value in (None, "", " ", 42):
        assert execute_intent(ParsedIntent("PLAN_FULFILLMENT", po_id=value)).error
    tool.assert_not_called()


def test_golden_ui_pipeline(monkeypatch):
    before = Repository().get_inventory_for_part("P-10036")
    monkeypatch.setattr(ui, "get_llm_status", lambda: LLMStatus("Configured", ""))
    client = FakeLLMClient()
    client.chat = Mock(side_effect=[
        LLMResponse('{"intent":"PLAN_FULFILLMENT","po_id":"PO-2026-1026"}', "mock"),
        LLMResponse("", "mock"),  # Exercise deterministic explanation fallback.
    ])
    monkeypatch.setattr(ui, "LLMClient", lambda: client)
    at = AppTest.from_string(
        "from ui import render_command_center\n"
        "render_command_center({'sha256':'test-fingerprint'})"
    ).run()
    assert client.chat.call_count == 0
    next(t for t in at.text_input if t.label == "Your request").set_value(
        "Can PO-2026-1026 be fulfilled?"
    )
    next(b for b in at.button if b.label == "Ask DealerBRAIN").click().run()
    assert not at.exception
    latest = at.session_state["latest"]
    assert latest["intent"] == "PLAN_FULFILLMENT"
    assert latest["view"]["tool"] == "plan_fulfillment"
    facts = latest["view"]["fulfillment"]
    assert facts["requested_qty"] == 20
    assert facts["network_available_qty"] == 4
    assert facts["planned_fulfillment_qty"] == 4
    assert facts["unresolved_remaining_qty"] == 16
    assert facts["allocations"] == [
        {"source_location": "WH-IN-PUN", "proposed_qty": 3},
        {"source_location": "WH-EU-FRA", "proposed_qty": 1},
    ]
    assert latest["view"]["status"]["label"] == "PARTIALLY_FULFILLABLE"
    assert "unresolved_remaining_qty: 16" in latest["message"]
    assert "plan only" in latest["message"]
    evidence = latest["view"]["evidence"]
    assert {"inventory", "parts", "purchase_orders"} <= {e["Source table"] for e in evidence}
    payload = client.chat.call_args.args[0][1]["content"]
    context = json.loads(payload.removeprefix("VERIFIED_CONTEXT="))
    assert context["facts"] == facts
    assert "dealer" not in json.dumps(context["facts"])
    assert {e["record_key"] for e in context["evidence"]} == {e["Record key"] for e in evidence}
    at.run()
    assert not at.exception
    assert client.chat.call_count == 2
    assert Repository().get_inventory_for_part("P-10036") == before
