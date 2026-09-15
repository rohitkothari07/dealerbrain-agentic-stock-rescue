"""Presentation metrics and prefill-only demo shortcuts."""

from unittest.mock import Mock

from streamlit.testing.v1 import AppTest

import ui
from llm_client import LLMClient
import transactions


def test_metrics():
    metadata = {"source_filename": "source.xlsx", "sha256": "abc", "tables": [
        {"table": "knowledge", "rows": 25, "columns": ["doc_id"]},
        {"table": "parts", "rows": 10, "columns": ["part_no"]},
    ], "issues": [{"table": "parts", "severity": "WARNING"}]}
    assert ui.control_tower_metrics(metadata) == {
        "Operational tables": 2, "Operational rows": 35, "Validation warnings": 1,
    }


def test_shortcuts_only_prefill(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Shortcut must not execute"))
    monkeypatch.setattr(LLMClient, "chat", forbidden)
    monkeypatch.setattr(transactions, "execute_simulated_fulfillment", forbidden)
    monkeypatch.setattr(transactions, "list_simulated_actions", lambda: [])
    for name, (_, check, label, key) in list(ui.ACTIONS.items()):
        monkeypatch.setitem(ui.ACTIONS, name, (forbidden, check, label, key))
    at = AppTest.from_string(
        "from ui import render_command_center\n"
        "render_command_center({'sha256':'test'})"
    ).run()
    for label, key, value in [
        ("Fulfillment demo", "fulfillment_po", "PO-2026-1026"),
        ("Governance scenario", "po_id", "PO-2026-1106"),
    ]:
        next(b for b in at.button if b.label == label).click().run()
        assert not at.exception
        assert at.session_state[key] == value
    for query in (*ui.QUICK_PROMPTS, *ui.KNOWLEDGE_EXAMPLES):
        next(b for b in at.button if b.label == query).click().run()
        assert not at.exception
        assert at.chat_input(key="copilot_question").value == query
    forbidden.assert_not_called()
    assert at.session_state["latest"] is None
