"""Synthetic rules tests over the real read-only repository implementation."""

import ast
from dataclasses import FrozenInstanceError
import hashlib
from pathlib import Path

import duckdb
import pytest

from repositories import Repository
from rules import DecisionStatus as Status, RulesEngine
from tests.test_repositories import database_path  # noqa: F401
import tools


@pytest.fixture
def ready_path(request):
    fixture_path = request.getfixturevalue("database_path")
    with duckdb.connect(str(fixture_path)) as db:
        db.execute("UPDATE dealers SET status = 'Active'")
        db.execute("UPDATE parts SET status = 'Active', warranty_months = 12")
        db.execute("UPDATE inventory SET available_qty = 5")
        db.execute(
            "UPDATE purchase_orders SET unit_price_eur = 12.5, currency = 'EUR', po_status = 'Open', shipment_id = 'S-test'"
        )
        db.execute("UPDATE shipments SET shipment_status = 'Delivered'")
        db.execute(
            "UPDATE claims SET po_no = 'PO-test', purchase_date = '2024-02-29', claim_date = '2025-02-28', reason = 'Defective'"
        )
    return fixture_path


@pytest.fixture
def engine(ready_path):
    return RulesEngine(Repository(ready_path))


def change(path, sql, parameters=None):
    with duckdb.connect(str(path)) as db:
        db.execute(sql, parameters or [])


def codes(result):
    return {i.code for i in result.issues}


@pytest.mark.parametrize(
    "requested,available,deficit,status", [(5, 10, 0, Status.PASS), (20, 10, 10, Status.WARNING)]
)
def test_stock_aggregation(engine, requested, available, deficit, status):
    result = engine.evaluate_stock_position("P-test", requested)
    assert result.status == status
    assert result.facts["available_qty"] == available
    assert result.facts["deficit_qty"] == deficit
    assert {e.source_table for e in result.evidence} == {"parts", "inventory"}
    assert len(result.evidence) == 3


def test_negative_inventory(engine, ready_path):
    change(ready_path, "UPDATE inventory SET available_qty = -2 WHERE inventory_id = 'I-b'")
    result = engine.evaluate_stock_position("P-test", 4)
    assert result.status == Status.WARNING
    assert result.facts["available_qty"] == 3
    assert result.facts["deficit_qty"] == 1
    assert "NEGATIVE_INVENTORY" in codes(result)


@pytest.mark.parametrize("qty", [0, -1, True, 2.5, "2", None])
def test_invalid_requested_quantity(engine, qty):
    assert engine.evaluate_stock_position("P-test", qty).status == Status.BLOCKED


def test_unknown_part_stock(engine):
    assert engine.evaluate_stock_position("missing", 1).status == Status.BLOCKED


@pytest.mark.parametrize(
    "setup,code",
    [
        ("DELETE FROM inventory", "MISSING_INVENTORY"),
        ("UPDATE inventory SET available_qty = NULL", "INVALID_INVENTORY"),
        ("UPDATE inventory SET inventory_id = 'same'", "AMBIGUOUS_INVENTORY"),
    ],
)
def test_unreliable_inventory(engine, ready_path, setup, code):
    change(ready_path, setup)
    result = engine.evaluate_stock_position("P-test", 1)
    assert result.status == Status.BLOCKED
    assert code in codes(result)
    assert "available_qty" not in result.facts


@pytest.mark.parametrize(
    "status,expected",
    [
        ("Active", Status.PASS),
        ("Suspended", Status.BLOCKED),
        ("Inactive", Status.BLOCKED),
        ("Unexpected", Status.BLOCKED),
        (None, Status.BLOCKED),
    ],
)
def test_dealer_status(engine, ready_path, status, expected):
    change(ready_path, "UPDATE dealers SET status = ? WHERE dealer_id = '001'", [status])
    result = engine.evaluate_dealer_eligibility("001")
    assert result.status == expected
    assert result.facts["dealer_status"] == status
    assert result.evidence[0].record_key == "001"


def test_unknown_dealer(engine):
    assert engine.evaluate_dealer_eligibility("missing").status == Status.BLOCKED


@pytest.mark.parametrize(
    "status,expected",
    [
        ("Active", Status.PASS),
        ("Discontinued", Status.BLOCKED),
        ("Inactive", Status.BLOCKED),
        ("Unexpected", Status.WARNING),
    ],
)
def test_part_status(engine, ready_path, status, expected):
    change(ready_path, "UPDATE parts SET status = ?", [status])
    assert engine.evaluate_part_eligibility("P-test").status == expected


def test_unknown_part(engine):
    assert engine.evaluate_part_eligibility("missing").status == Status.BLOCKED


def test_valid_po_and_shared_stock(engine):
    result = engine.evaluate_purchase_order("PO-test")
    assert result.status == Status.PASS
    assert len(result.facts["lines"]) == 2
    assert result.facts["stock_positions"]["P-test"].facts["requested_qty"] == 5
    assert {e.source_table for e in result.evidence} == {
        "purchase_orders",
        "parts",
        "dealers",
        "inventory",
    }


def test_shared_stock_is_not_double_counted(engine, ready_path):
    change(ready_path, "UPDATE inventory SET available_qty = 2 WHERE part_no = 'P-test'")
    result = engine.evaluate_purchase_order("PO-test")
    assert result.status == Status.WARNING
    assert result.facts["stock_positions"]["P-test"].facts["deficit_qty"] == 1


@pytest.mark.parametrize(
    "sql,code",
    [
        ("UPDATE dealers SET status = 'Suspended'", "DEALER_INELIGIBLE"),
        ("UPDATE parts SET status = 'Discontinued'", "PART_INELIGIBLE"),
        ("UPDATE purchase_orders SET order_qty = 0", "NON_POSITIVE_QUANTITY"),
        ("UPDATE purchase_orders SET unit_price_eur = 0", "NON_POSITIVE_PRICE"),
        ("UPDATE purchase_orders SET dealer_id = 'missing'", "UNKNOWN_DEALER"),
        ("UPDATE purchase_orders SET part_no = 'missing'", "UNKNOWN_PART"),
        ("UPDATE purchase_orders SET po_line_no = 1", "AMBIGUOUS_PO_LINE"),
    ],
)
def test_po_blockers(engine, ready_path, sql, code):
    change(ready_path, sql)
    result = engine.evaluate_purchase_order("PO-test")
    assert result.status == Status.BLOCKED
    assert code in codes(result)
    assert result.evidence


def test_currency_is_only_warning(engine, ready_path):
    change(ready_path, "UPDATE purchase_orders SET currency = 'USD'")
    result = engine.evaluate_purchase_order("PO-test")
    assert result.status == Status.WARNING
    assert "CURRENCY_MISMATCH" in codes(result)


def test_unknown_po(engine):
    assert engine.evaluate_purchase_order("missing").status == Status.BLOCKED


def test_valid_claim_link(engine):
    result = engine.evaluate_claim_consistency("C-a")
    assert result.status == Status.PASS
    assert result.facts["shipments"][0].shipment_id == "S-test"
    assert {e.source_table for e in result.evidence} == {
        "claims",
        "dealers",
        "parts",
        "purchase_orders",
        "shipments",
    }


@pytest.mark.parametrize(
    "sql,code",
    [
        ("UPDATE purchase_orders SET shipment_id = 'missing'", "MISSING_SHIPMENT_REFERENCE"),
        ("UPDATE purchase_orders SET shipment_id = ''", "MISSING_SHIPMENT_REFERENCE"),
        ("UPDATE claims SET reason = 'Not Received'", "SHIPMENT_CLAIM_CONTRADICTION"),
        ("UPDATE shipments SET dealer_id = 'different'", "SHIPMENT_REFERENCE_MISMATCH"),
        ("UPDATE claims SET part_no = 'missing'", "UNKNOWN_PART"),
        ("UPDATE claims SET dealer_id = 'missing'", "UNKNOWN_DEALER"),
    ],
)
def test_claim_warnings_do_not_reject(engine, ready_path, sql, code):
    change(ready_path, sql)
    result = engine.evaluate_claim_consistency("C-a")
    assert result.status == Status.WARNING
    assert code in codes(result)
    assert result.evidence


def test_claim_does_not_infer_unrelated_shipment(engine, ready_path):
    change(ready_path, "UPDATE claims SET reason = 'Not Received', part_no = 'other'")
    result = engine.evaluate_claim_consistency("C-a")
    assert "SHIPMENT_CLAIM_CONTRADICTION" not in codes(result)
    assert result.facts["shipments"] == ()


@pytest.mark.parametrize(
    "purchase,claimed,months,end,within",
    [
        ("2024-02-29", "2025-02-28", 12, "2025-02-28", True),
        ("2024-02-29", "2025-03-01", 12, "2025-02-28", False),
        ("2024-01-31", "2024-02-29", 1, "2024-02-29", True),
        ("2023-01-31", "2023-03-01", 1, "2023-02-28", False),
    ],
)
def test_warranty_calendar_months(engine, ready_path, purchase, claimed, months, end, within):
    change(ready_path, "UPDATE claims SET purchase_date = ?, claim_date = ?", [purchase, claimed])
    change(ready_path, "UPDATE parts SET warranty_months = ?", [months])
    result = engine.evaluate_warranty("C-a")
    assert result.facts["warranty_end_date"] == end
    assert result.facts["within_warranty"] is within
    assert result.status == (Status.PASS if within else Status.WARNING)


def test_warranty_missing_and_invalid_date_order(engine, ready_path):
    change(ready_path, "UPDATE claims SET purchase_date = 'invalid'")
    assert engine.evaluate_warranty("C-a").status == Status.NOT_APPLICABLE
    change(ready_path, "UPDATE claims SET purchase_date = '2026-01-01'")
    assert engine.evaluate_warranty("C-a").status == Status.BLOCKED


def test_scanner_discovers_general_anomalies(engine, ready_path):
    change(ready_path, "UPDATE inventory SET available_qty = -1 WHERE inventory_id = 'I-a'")
    change(ready_path, "UPDATE dealers SET status = 'Suspended'")
    change(ready_path, "UPDATE parts SET status = 'Discontinued'")
    change(ready_path, "UPDATE purchase_orders SET order_qty = 0, unit_price_eur = -1")
    change(ready_path, "UPDATE claims SET reason = 'Not Received'")
    issues = engine.scan_known_data_quality_issues()
    assert {
        "NEGATIVE_INVENTORY",
        "NON_POSITIVE_QUANTITY",
        "NON_POSITIVE_PRICE",
        "SUSPENDED_DEALER_ORDER",
        "DISCONTINUED_PART_ORDER",
        "SHIPMENT_CLAIM_CONTRADICTION",
    } <= {i.code for i in issues}
    assert all(i.evidence for i in issues)
    assert issues == engine.scan_known_data_quality_issues()
    change(ready_path, "UPDATE purchase_orders SET po_status = 'Delivered'")
    assert not {"SUSPENDED_DEALER_ORDER", "DISCONTINUED_PART_ORDER"} & {
        i.code for i in engine.scan_known_data_quality_issues()
    }


def test_immutable_results(engine):
    result = engine.evaluate_purchase_order("PO-test")
    with pytest.raises(FrozenInstanceError):
        result.summary = "changed"
    with pytest.raises(TypeError):
        result.facts["po_no"] = "changed"
    with pytest.raises(TypeError):
        result.facts["lines"][0]["po"] = None


def test_tools_safety_and_database_unchanged(engine, ready_path):
    before = hashlib.sha256(ready_path.read_bytes()).hexdigest()
    assert tools.check_stock("P-test", 1, engine=engine).status == Status.PASS
    assert tools.check_stock("P-test", 0, engine=engine).status == Status.BLOCKED
    for function in [
        tools.check_po,
        tools.check_dealer,
        tools.check_part,
        tools.check_claim,
        tools.check_warranty,
    ]:
        for value in ["ANSWER_KEY", "' OR 1=1 --", "", None]:
            assert function(value, engine=engine).status == Status.BLOCKED
    issues = tools.scan_anomalies(engine=engine)
    assert all(e.source_table not in {"ANSWER_KEY", "README"} for i in issues for e in i.evidence)
    assert hashlib.sha256(ready_path.read_bytes()).hexdigest() == before
    for filename in ["rules.py", "tools.py"]:
        source = (Path(__file__).resolve().parents[1] / filename).read_text()
        tree = ast.parse(source)
        imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        assert not imports & {"database", "duckdb", "llm_client", "streamlit"}
        assert "ANSWER_KEY" not in source
        assert "PO-2026-" not in source
        assert not any(
            isinstance(node, ast.Attribute)
            and node.attr in {"execute", "connect", "write_bytes", "write_text"}
            for node in ast.walk(tree)
        )


def test_negative_stock_warns_even_when_demand_is_met(engine, ready_path):
    change(ready_path, "UPDATE inventory SET available_qty = -1 WHERE inventory_id = 'I-b'")
    result = engine.evaluate_stock_position("P-test", 1)
    assert result.status == Status.WARNING
    assert result.facts["deficit_qty"] == 0
    assert result.facts["available_qty"] == 4


def test_blank_inventory_key_blocks(engine, ready_path):
    change(ready_path, "UPDATE inventory SET inventory_id = '' WHERE inventory_id = 'I-b'")
    assert engine.evaluate_stock_position("P-test", 1).status == Status.BLOCKED


def test_warranty_missing_part_is_not_applicable(engine, ready_path):
    change(ready_path, "UPDATE claims SET part_no = 'missing'")
    assert engine.evaluate_warranty("C-a").status == Status.NOT_APPLICABLE


def test_scanner_unknown_references(engine, ready_path):
    change(
        ready_path,
        "UPDATE purchase_orders SET dealer_id = 'missing', part_no = 'missing', shipment_id = 'missing'",
    )
    issues = engine.scan_known_data_quality_issues()
    assert {"UNKNOWN_DEALER", "UNKNOWN_PART", "MISSING_SHIPMENT_REFERENCE"} <= {
        i.code for i in issues
    }
    assert all(i.evidence for i in issues)


def test_tool_delegation(engine):
    assert tools.check_po("PO-test", engine=engine) == engine.evaluate_purchase_order("PO-test")
    assert tools.check_part("P-test", engine=engine) == engine.evaluate_part_eligibility("P-test")
    assert tools.check_dealer("001", engine=engine) == engine.evaluate_dealer_eligibility("001")
    assert tools.check_claim("C-a", engine=engine) == engine.evaluate_claim_consistency("C-a")
    assert tools.check_warranty("C-a", engine=engine) == engine.evaluate_warranty("C-a")
