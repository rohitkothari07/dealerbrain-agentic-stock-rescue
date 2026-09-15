"""Presentation-only evidence formatting and response placement."""

from copy import deepcopy
from unittest.mock import Mock

from streamlit.testing.v1 import AppTest

from llm_client import LLMClient
from models import EvidenceRef
from rules import DecisionResult, DecisionStatus
import transactions
import ui


def test_composite_key_preserved():
    ref = EvidenceRef("purchase_orders", '["PO-2026-1026", 1]')
    view = ui._execution_view(DecisionResult(DecisionStatus.PASS, "", {}, (), (ref,)),
                              "Check", "check_po")
    before = deepcopy(view)
    display = ui._display_evidence(view["evidence"])
    assert display[0]["Record key"] == "PO-2026-1026 · Line 1"
    assert view == before
    assert ref.record_key == '["PO-2026-1026", 1]'
    for key in ('not-json', '["PO", null]', '["PO", true]'):
        rows = [{"Source table": "purchase_orders", "Record key": key}]
        assert ui._display_evidence(rows) == rows


def test_response_precedes_guided_checks_without_calls(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Render cannot execute"))
    monkeypatch.setattr(LLMClient, "chat", forbidden)
    monkeypatch.setattr(transactions, "execute_simulated_fulfillment", forbidden)
    monkeypatch.setattr(transactions, "list_simulated_actions", lambda: [])
    at = AppTest.from_string('''
import streamlit as st
from ui import render_command_center, _execution_view
from rules import DecisionResult, DecisionStatus
st.session_state.setdefault("dataset_sha", "test")
st.session_state.setdefault("history", [])
st.session_state.setdefault("latest", {
    "view": _execution_view(DecisionResult(DecisionStatus.PASS, "Verified", {}, (), ()),
                            "CHECK_PO", "check_po"),
    "message": "Grounded explanation", "question": "Check my PO", "inputs": [], "time": "test", "fingerprint": "test",
})
render_command_center({"sha256": "test"})
''').run()
    assert not at.exception
    headings = [h.value for h in at.subheader]
    assert headings.index("Grounded DealerBRAIN Response") < headings.index("Guided checks")
    assert not any("DETERMINISTIC COPILOT RESULT" in c.value for c in at.caption)
    assert not any("Last completed check" in c.value for c in at.caption)
    assert "Evidence & Decision Trace" in headings
    assert len(at.chat_message) == 2
    assert at.chat_message[0].name == "user"
    assert at.chat_message[1].name == "assistant"
    assert any(t.value == "Check my PO" for t in at.chat_message[0].text)
    assert any(t.value == "Grounded explanation" for t in at.chat_message[1].text)
    assert not next(e for e in at.expander if e.label == "Deterministic actions").proto.expanded
    at.run()
    forbidden.assert_not_called()
