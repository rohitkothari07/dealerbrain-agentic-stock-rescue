"""Confirmation wiring with temporary SQLite only; no browser or provider calls."""

from dataclasses import replace
from unittest.mock import Mock

from streamlit.testing.v1 import AppTest

from llm_client import LLMClient
from repositories import Repository
import transactions


def test_confirm_workflow(tmp_path, monkeypatch):
    path = tmp_path / "actions.sqlite"
    original = transactions.execute_simulated_fulfillment
    execute = Mock(side_effect=lambda po, plan, *, confirmed: original(
        po, plan, confirmed=confirmed, store_path=path,
    ))
    monkeypatch.setattr(transactions, "execute_simulated_fulfillment", execute)
    read = transactions.list_simulated_actions
    monkeypatch.setattr(transactions, "list_simulated_actions", lambda: read(store_path=path))
    monkeypatch.setenv("LLM_ENABLED", "false")
    llm = Mock(side_effect=AssertionError("No LLM allowed"))
    monkeypatch.setattr(LLMClient, "chat", llm)
    repo = Repository()
    inventory = repo.get_inventory_for_part("P-10036")
    po = repo.get_purchase_order("PO-2026-1026")
    at = AppTest.from_string(
        "from ui import render_command_center\n"
        "render_command_center({'sha256':'test-fingerprint'})"
    ).run()
    at.selectbox[0].select("Plan Fulfillment").run()
    at.text_input(key="fulfillment_po").set_value("PO-2026-1026")
    next(b for b in at.button if b.label == "Run check").click().run()
    assert not at.exception
    assert not path.exists()
    at.run()
    execute.assert_not_called()
    assert not path.exists()
    at.button(key="confirm_simulated_fulfillment").click().run()
    assert not at.exception
    execute.assert_called_once()
    assert execute.call_args.kwargs == {"confirmed": True}
    outcome = at.session_state["latest"]["action_result"]
    assert outcome.status == "CREATED"
    assert outcome.action.status == "POC_SIMULATED"
    assert any(outcome.action.action_id in t.value for t in at.text)
    assert any("Remaining unresolved: 16" in t.value for t in at.text)
    assert len(read(store_path=path)) == 1
    at.run()
    assert execute.call_count == 1
    at.button(key="confirm_simulated_fulfillment").click().run()
    assert at.session_state["latest"]["action_result"].status == "ALREADY_EXISTS"
    assert len(read(store_path=path)) == 1
    assert repo.get_inventory_for_part("P-10036") == inventory
    assert repo.get_purchase_order("PO-2026-1026") == po
    llm.assert_not_called()
    # A changed/tampered plan is rejected by the transaction boundary, not the UI.
    latest = at.session_state["latest"]
    latest["plan"] = replace(latest["plan"], remaining_qty=999)
    at.button(key="confirm_simulated_fulfillment").click().run()
    assert not at.exception
    assert at.session_state["latest"]["action_result"].status == "REJECTED"
    assert any("stale, altered, or ineligible" in e.value for e in at.error)
    assert len(read(store_path=path)) == 1


def test_history_missing_store_does_not_create(tmp_path):
    path = tmp_path / "absent.sqlite"
    assert transactions.list_simulated_actions(store_path=path) == []
    assert not path.exists()
