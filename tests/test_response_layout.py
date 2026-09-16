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
    nodes = list(at.main)
    guided = next(e for e in at.expander if e.label == "Guided checks · advanced")
    assert nodes.index(at.chat_message[1]) < nodes.index(guided)
    assert not any("DETERMINISTIC COPILOT RESULT" in c.value for c in at.caption)
    assert not any("Last completed check" in c.value for c in at.caption)
    trace = next(e for e in at.chat_message[1].expander if e.label == "Evidence & Decision Trace")
    assert not trace.proto.expanded
    assert len(at.chat_message) == 2
    assert at.chat_message[0].name == "user"
    assert at.chat_message[1].name == "assistant"
    assert any(t.value == "Check my PO" for t in at.chat_message[0].text)
    assert any(t.value == "Grounded explanation" for t in at.chat_message[1].text)
    assert not guided.proto.expanded
    at.run()
    forbidden.assert_not_called()


def test_newest_exchange_first_without_reordering_or_replaying_history(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Rendering cannot execute tools or actions"))
    monkeypatch.setattr(LLMClient, "chat", forbidden)
    monkeypatch.setattr(ui, "execute_intent", forbidden)
    monkeypatch.setattr(transactions, "execute_simulated_fulfillment", forbidden)
    monkeypatch.setattr(transactions, "list_simulated_actions", lambda: [])
    for name, (function, *_) in ui.ACTIONS.items():
        monkeypatch.setattr(ui.tools, function.__name__, forbidden)
        monkeypatch.setitem(ui.ACTIONS, name, (forbidden, *ui.ACTIONS[name][1:]))

    def submit(question, client, previous):
        return {
            "message": f"Answer: {question}", "intent": "GENERAL_CHAT", "view": None,
            "plan": None, "po_id": None,
        }

    pipeline = Mock(side_effect=submit)
    monkeypatch.setattr(ui, "run_copilot", pipeline)
    at = AppTest.from_string(
        "from ui import render_command_center\nrender_command_center({'sha256':'timeline'})"
    ).run()
    questions = ["Hi", ui.QUICK_PROMPTS[0], "Why?"]
    for count, question in enumerate(questions, start=1):
        if count == 2:
            at.button(key=f"quick_{question}").click().run()
            assert pipeline.call_count == 1  # A suggested prompt only prefills.
        at.chat_input(key="copilot_question").set_value(question).run()
        assert not at.exception
        expected = [
            (role, text)
            for text in reversed(questions[:count])
            for role, text in (("user", text), ("assistant", f"Answer: {text}"))
        ]
        assert [(m.name, m.text[0].value) for m in at.chat_message] == expected
        history = at.session_state["history"]
        assert [r["question"] for r in history] == questions[:count]
        assert at.session_state["latest"] == history[-1]
        previous = pipeline.call_args.args[2]
        assert previous == (history[-2] if count > 1 else None)
        assert pipeline.call_count == count
        # The input remains above the newest exchange in the rendered workspace.
        nodes = list(at.main)
        assert nodes.index(at.chat_input[0]) < nodes.index(at.chat_message[0])
        before = deepcopy(history)
        at.run()
        assert not at.exception
        assert at.session_state["history"] == before
        assert [(m.name, m.text[0].value) for m in at.chat_message] == expected
        assert pipeline.call_count == count

    # Clearing conversation state also clears the reversed presentation.
    at.session_state["history"] = []
    at.session_state["latest"] = None
    at.run()
    assert not at.exception
    assert not at.chat_message
    assert at.session_state["history"] == []
    assert any("Ask naturally" in item.value for item in at.info)
    assert pipeline.call_count == 3
    forbidden.assert_not_called()
