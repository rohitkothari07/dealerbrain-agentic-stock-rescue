"""Judge-facing presentation contracts, using existing deterministic operations."""

from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

import app
from llm_client import LLMClient
import transactions
import ui


@pytest.fixture(autouse=True)
def passive_render(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Rendering cannot call AI or write actions"))
    monkeypatch.setattr(LLMClient, "chat", forbidden)
    monkeypatch.setattr(transactions, "execute_simulated_fulfillment", forbidden)
    monkeypatch.setattr(transactions, "list_simulated_actions", lambda: [])
    monkeypatch.setenv("LLM_ENABLED", "false")
    yield
    forbidden.assert_not_called()


def test_identity_chat_and_secondary_evidence():
    at = AppTest.from_file(app.__file__).run()
    assert not at.exception
    assert at.title[0].value == "DealerBRAIN"
    assert any(s.value == "After-Sales Stock Rescue Copilot" for s in at.markdown)
    assert any("Grounded decisions. Visible evidence. Human approval." in c.value for c in at.caption)
    assert any("i.mobilothon 6.0" in c.value for c in at.caption)
    assert any("POC Mode" in m.value for m in at.sidebar.markdown)
    assert len(at.chat_input) == 1
    assert not any(e.label == "Evidence & Decision Trace" for e in at.expander)
    assert not next(e for e in at.expander if e.label == "Guided checks · advanced").proto.expanded
    assert not next(e for e in at.expander if e.label == "System diagnostics").proto.expanded
    support = [m.value for m in at.markdown if '<aside class="db-support">' in m.value]
    assert len(support) == 2
    assert "DealerBRAIN Tips" in support[0] and "Demo & Trust" in support[1]
    assert len([b for b in at.button if b.key and b.key.startswith("quick_")]) == 4
    assert not at.get("file_uploader")
    assert not at.get("video")
    assert not any("image" in b.label.lower() for b in at.button)
    at.run()
    assert not at.exception


def run_check(action, key, value):
    at = AppTest.from_string(
        "from ui import render_command_center\nrender_command_center({'sha256':'test'})"
    ).run()
    at.selectbox[0].select(action).run()
    at.text_input(key=key).set_value(value)
    next(b for b in at.button if b.label == "Run check").click().run()
    assert not at.exception
    return at


def test_fulfillment_result_and_confirmation_boundary():
    at = run_check("Plan Fulfillment", "fulfillment_po", "PO-2026-1026")
    facts = at.session_state["latest"]["view"]["fulfillment"]
    assert {key: facts[key] for key in (
        "requested_qty", "network_available_qty", "planned_fulfillment_qty", "unresolved_remaining_qty",
    )} == {"requested_qty": 20, "network_available_qty": 4,
          "planned_fulfillment_qty": 4, "unresolved_remaining_qty": 16}
    assert facts["allocations"] == [
        {"source_location": "WH-IN-PUN", "proposed_qty": 3},
        {"source_location": "WH-EU-FRA", "proposed_qty": 1},
    ]
    assert any("Human approval required" in m.value for m in at.markdown)
    assert any("POC_SIMULATED" in c.value and "no ERP" in c.value for c in at.caption)
    assert at.button(key="confirm_simulated_fulfillment")
    assert "action_result" not in at.session_state["latest"]
    at.run()
    assert not at.exception


def test_governance_remains_blocked():
    at = run_check("Evaluate Purchase Order", "po_id", "PO-2026-1106")
    assert at.session_state["latest"]["view"]["status"]["label"] == "BLOCKED"
    assert any("Blocked — governance" in e.value for e in at.error)
    dealers = at.session_state["latest"]["view"]["dealers"]
    assert any(d["Dealer"] == "D007" and d["Source status"].lower() == "suspended" for d in dealers)
    assert not any(b.key == "confirm_simulated_fulfillment" for b in at.button)


def test_data_error_does_not_expose_exception(monkeypatch):
    monkeypatch.setattr(app, "initialize_data", Mock(side_effect=app.DataInitializationError("PRIVATE")))
    at = AppTest.from_string("from app import main\nmain()").run()
    assert not at.exception
    assert "PRIVATE" not in at.error[0].value
    assert "Operational data is unavailable" in at.error[0].value


def test_chat_business_result_precedes_explanation():
    at = AppTest.from_string('''
import streamlit as st
from ui import render_command_center, _execution_view
from tools import plan_fulfillment
plan = plan_fulfillment(po_id="PO-2026-1026")
st.session_state.setdefault("dataset_sha", "test")
st.session_state.setdefault("history", [])
st.session_state.setdefault("latest", {
    "view": _execution_view(plan, "PLAN_FULFILLMENT", "plan_fulfillment", "PO-2026-1026"),
    "plan": plan, "po_id": "PO-2026-1026", "question": "Can this PO be fulfilled?",
    "message": "Grounded explanation appears after the business outcome.",
    "inputs": [], "time": "test", "fingerprint": "test",
})
render_command_center({"sha256":"test"})
''').run()
    assert not at.exception
    assert [m.name for m in at.chat_message] == ["user", "assistant"]
    assistant = at.chat_message[1]
    assert any("Partial fulfillment" in m.value for m in assistant.markdown)
    assert any("4 of 20 units can be fulfilled" in m.value for m in assistant.markdown)
    assistant_markup = "\n".join(m.value for m in assistant.markdown)
    assert "Requested Quantity" in assistant_markup and "Unresolved Quantity" in assistant_markup
    assert any(t.value == "Grounded explanation appears after the business outcome." for t in assistant.text)
    assert any("Recommended next step" in m.value for m in at.markdown)
    assert at.button(key="confirm_simulated_fulfillment")
    assert not next(e for e in at.expander if e.label == "Evidence & Decision Trace").proto.expanded
    assert not next(e for e in assistant.expander if e.label == "Facts & details").proto.expanded
    nodes = list(assistant)
    outcome = next(m for m in assistant.markdown if "Partial fulfillment" in m.value)
    metrics = next(m for m in assistant.markdown if "Requested Quantity" in m.value)
    approval = assistant.button(key="confirm_simulated_fulfillment")
    details = next(e for e in assistant.expander if e.label == "Facts & details")
    assert nodes.index(outcome) < nodes.index(metrics) < nodes.index(approval) < nodes.index(details)


def test_casual_reply_is_lightweight():
    at = AppTest.from_string(
        "from ui import render_command_center\nrender_command_center({'sha256':'casual'})"
    ).run()
    at.chat_input(key="copilot_question").set_value("Hi").run()
    assert not at.exception
    assert [m.name for m in at.chat_message] == ["user", "assistant"]
    assistant = at.chat_message[1]
    assert "DealerBRAIN" in assistant.text[0].value
    assert not assistant.expander and not assistant.metric and not assistant.button
    assert not assistant.dataframe and not assistant.subheader
    at.run()
    assert not at.exception


def test_guided_result_visible_above_previous_chat():
    at = AppTest.from_string(
        "from ui import render_command_center\nrender_command_center({'sha256':'mixed'})"
    ).run()
    at.chat_input(key="copilot_question").set_value("Hi").run()
    at.selectbox[0].select("Plan Fulfillment").run()
    at.text_input(key="fulfillment_po").set_value("PO-2026-1026")
    next(b for b in at.button if b.label == "Run check").click().run()
    assert not at.exception
    assert "Partial fulfillment" in " ".join(m.value for m in at.chat_message[0].markdown)
    assert at.chat_message[0].button(key="confirm_simulated_fulfillment")
    assert at.chat_message[1].text[0].value == "Hi"
    assert at.session_state["history"][0]["question"] == "Hi"
    assert len(ui.ACTIONS) == len(at.selectbox[0].options)
