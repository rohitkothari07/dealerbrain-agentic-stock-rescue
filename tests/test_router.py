"""Focused dispatch checks plus one operational-data smoke test."""

from unittest.mock import Mock

import pytest

from intent import ParsedIntent
from models import EvidenceRef
from router import execute_intent
from rules import DecisionResult, DecisionStatus, RulesEngine
import tools


@pytest.fixture
def mocked_tools(monkeypatch):
    mocks = {}
    for name in (
        "check_po", "check_stock", "check_dealer", "check_part", "check_claim",
        "scan_anomalies", "check_warranty",
    ):
        mocks[name] = Mock(name=name)
        monkeypatch.setattr(tools, name, mocks[name])
    return mocks


@pytest.mark.parametrize("parsed, name, args", [
    (ParsedIntent("CHECK_PO", po_id="PO-2026-1026"), "check_po", ("PO-2026-1026",)),
    (ParsedIntent("CHECK_STOCK", part_no="P-10036", requested_qty=20),
     "check_stock", ("P-10036", 20)),
    (ParsedIntent("CHECK_DEALER", dealer_id="D007"), "check_dealer", ("D007",)),
    (ParsedIntent("CHECK_PART", part_no="P-10036"), "check_part", ("P-10036",)),
    (ParsedIntent("CHECK_CLAIM", claim_id="C001"), "check_claim", ("C001",)),
    (ParsedIntent("SCAN_ANOMALIES"), "scan_anomalies", ()),
])
def test_routes_and_preserves_result(parsed, name, args, mocked_tools):
    result = (
        [] if name == "scan_anomalies" else DecisionResult(
            DecisionStatus.WARNING, "Existing result", {}, (),
            (EvidenceRef("parts", "P-10036"),),
        )
    )
    mocked_tools[name].return_value = result
    execution = execute_intent(parsed)
    assert execution.intent == parsed.intent
    assert execution.tool_name == name
    assert execution.result is result
    assert execution.error is None
    if isinstance(result, DecisionResult):
        assert execution.result.evidence is result.evidence
    mocked_tools[name].assert_called_once_with(*args)
    for other, mock in mocked_tools.items():
        if other != name:
            mock.assert_not_called()


@pytest.mark.parametrize("intent, field", [
    ("CHECK_PO", "po_id"), ("CHECK_STOCK", "part_no"),
    ("CHECK_DEALER", "dealer_id"), ("CHECK_PART", "part_no"),
    ("CHECK_CLAIM", "claim_id"),
])
@pytest.mark.parametrize("value", [None, "", " \t", 123])
def test_invalid_identifier_executes_nothing(intent, field, value, mocked_tools):
    parsed = ParsedIntent(intent, **{field: value}, requested_qty=20)
    execution = execute_intent(parsed)
    assert execution.error
    assert execution.result is None
    assert execution.tool_name is None
    for mock in mocked_tools.values():
        mock.assert_not_called()


@pytest.mark.parametrize("quantity", [None, 0, -1, True, 1.5, "20"])
def test_invalid_stock_quantity(quantity, mocked_tools):
    execution = execute_intent(
        ParsedIntent("CHECK_STOCK", part_no="P-10036", requested_qty=quantity)
    )
    assert execution.error
    assert execution.result is None
    for mock in mocked_tools.values():
        mock.assert_not_called()


@pytest.mark.parametrize("intent", ["UNKNOWN", "check_po", "CHECK_WARRANTY", "__import__", ""])
def test_no_unapproved_execution(intent, mocked_tools):
    execution = execute_intent(ParsedIntent(intent, po_id="PO-2026-1026"))
    assert execution.tool_name is None
    assert execution.result is None
    assert (execution.error is None) == (intent == "UNKNOWN")
    for mock in mocked_tools.values():
        mock.assert_not_called()


def test_backend_failure_is_safe(mocked_tools):
    mocked_tools["check_po"].side_effect = RuntimeError("private backend details")
    execution = execute_intent(ParsedIntent("CHECK_PO", po_id="PO-2026-1026"))
    assert execution.tool_name == "check_po"
    assert execution.result is None
    assert execution.error == "Deterministic tool execution failed."


def test_real_purchase_order_matches_business_engine():
    parsed = ParsedIntent("CHECK_PO", po_id="PO-2026-1026")
    execution = execute_intent(parsed)
    expected = RulesEngine().evaluate_purchase_order(parsed.po_id)
    assert execution.error is None
    assert execution.tool_name == "check_po"
    assert execution.result == expected
    assert execution.result.facts["po_no"] == parsed.po_id
    assert execution.result.facts["lines"]
    assert execution.result.facts["stock_positions"]
    assert execution.result.evidence
    assert any(ref.source_table == "purchase_orders" for ref in execution.result.evidence)
