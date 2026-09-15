"""Focused offline UI integration checks."""

import importlib
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

import app
from llm_client import FakeLLMClient, LLMClient, LLMStatus
from models import EvidenceRef
from rules import DecisionResult, DecisionStatus
import tools
import ui


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch):
    monkeypatch.setattr(LLMClient, "chat", Mock(side_effect=AssertionError("Real LLM forbidden")))


def decision():
    return DecisionResult(DecisionStatus.PASS, "Verified", {"dealer_id": "D007"}, (),
                          (EvidenceRef("dealers", "D007"),))


def test_pipeline_order_and_evidence(monkeypatch):
    monkeypatch.setattr(ui, "get_llm_status", lambda: LLMStatus("Configured", ""))
    client = FakeLLMClient()
    client.chat = Mock(side_effect=[
        FakeLLMClient('{"intent":"CHECK_DEALER","dealer_id":"D007"}')._response,
        FakeLLMClient("Explanation with no provenance authority")._response,
    ])
    monkeypatch.setattr(tools, "check_dealer", Mock(return_value=decision()))
    order = []
    for name in ("parse_intent", "execute_intent", "generate_response"):
        original = getattr(ui, name)

        def wrapper(*args, _name=name, _original=original):
            order.append(_name)
            return _original(*args)

        monkeypatch.setattr(ui, name, wrapper)
    result = ui.run_copilot("Check dealer D007", client)
    assert order == ["parse_intent", "execute_intent", "generate_response"]
    assert client.chat.call_count == 2
    assert result["view"]["evidence"] == [{"Source table": "dealers", "Record key": "D007"}]
    assert result["intent"] == "CHECK_DEALER"
    assert result["view"]["status"]["label"] == "PASS"


@pytest.mark.parametrize("state", ["Disabled", "Not Configured"])
def test_disabled_message(monkeypatch, state):
    monkeypatch.setattr(ui, "get_llm_status", lambda: LLMStatus(state, ""))
    client = FakeLLMClient()
    result = ui.run_copilot("Check PO", client)
    assert "requires configured AI" in result["message"]
    assert result["view"] is None
    assert client.usage.snapshot().request_count == 0


def test_startup_rerender_and_guided_action_without_llm(monkeypatch):
    monkeypatch.setattr(ui, "get_llm_status", lambda: LLMStatus("Disabled", ""))
    importlib.reload(app)
    assert LLMClient.chat.call_count == 0
    guided = Mock(return_value=decision())
    guided.__name__ = "check_dealer"
    monkeypatch.setitem(ui.ACTIONS, "Check Dealer", (guided, "dealer_check", "Dealer ID", "dealer_id"))
    at = AppTest.from_string(
        "from ui import render_command_center\n"
        "render_command_center({'sha256': 'test-fingerprint'})"
    ).run()
    assert not at.exception
    at.selectbox[0].select("Check Dealer").run()
    at.text_input(key="dealer_id").set_value("D007")
    next(b for b in at.button if b.label == "Run check").click().run()
    assert not at.exception
    guided.assert_called_once_with("D007")
    at.run()
    assert guided.call_count == 1
    assert LLMClient.chat.call_count == 0
    assert len(at.selectbox[0].options) == 6


def test_copilot_submission_not_repeated_on_rerender(monkeypatch):
    monkeypatch.setattr(ui, "get_llm_status", lambda: LLMStatus("Configured", ""))
    client = FakeLLMClient()
    client.chat = Mock(side_effect=[
        FakeLLMClient('{"intent":"CHECK_DEALER","dealer_id":"D007"}')._response,
        FakeLLMClient("Dealer check completed.")._response,
    ])
    monkeypatch.setattr(ui, "LLMClient", lambda: client)
    monkeypatch.setattr(tools, "check_dealer", Mock(return_value=decision()))
    at = AppTest.from_string(
        "from ui import render_command_center\n"
        "render_command_center({'sha256': 'test-fingerprint'})"
    ).run()
    assert client.chat.call_count == 0
    next(t for t in at.text_input if t.label == "Your request").set_value("Check dealer D007")
    next(b for b in at.button if b.label == "Ask DealerBRAIN").click().run()
    assert not at.exception
    assert client.chat.call_count == 2
    assert at.session_state["latest"]["intent"] == "CHECK_DEALER"
    assert at.session_state["latest"]["view"]["evidence"][0]["Record key"] == "D007"
    at.run()
    assert not at.exception
    assert client.chat.call_count == 2
    assert len(at.session_state["history"]) == 1
