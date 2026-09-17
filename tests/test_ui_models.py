"""Pure presentation tests; no Streamlit automation or external services."""

import pytest

from models import EvidenceRef, InventoryRecord, Part, RepositoryResult
from rules import BusinessIssue, DecisionResult, DecisionStatus
from ui_models import (
    dataset_view, format_evidence, format_facts, format_status, inventory_dashboard_view,
    result_view,
)


def view(result):
    return result_view(result, action="Check", tool="check_stock", check="evaluate_stock_position")


@pytest.mark.parametrize(
    "status,style",
    [("PASS", "success"), ("WARNING", "warning"), ("BLOCKED", "error"), ("NOT_APPLICABLE", "info")],
)
def test_status_formatting(status, style):
    assert format_status(status) == {"label": status, "style": style}
    assert format_status(DecisionStatus(status))["label"] == status


def test_unknown_status_rejected():
    with pytest.raises(ValueError):
        format_status("APPROVED_BY_AI")


def test_decision_view_is_deterministic_and_preserves_zero_and_negative():
    result = DecisionResult(
        DecisionStatus.WARNING,
        "Source summary.",
        {"part_no": "P-test", "requested_qty": 1, "available_qty": -2, "deficit_qty": 3},
        (),
        (EvidenceRef("parts", "P-test"), EvidenceRef("inventory", "I-test")),
    )
    assert view(result) == view(result)
    rendered = view(result)
    assert rendered["summary"] == "Source summary."
    assert rendered["stocks"][0]["available_qty"] == -2
    assert format_facts({"zero": 0, "flag": False}) == [
        {"Fact": "zero", "Value": "0"},
        {"Fact": "flag", "Value": "False"},
    ]


def test_evidence_deduplicates_without_changing_keys():
    evidence = EvidenceRef("purchase_orders", '["PO-test",1]')
    assert format_evidence([evidence, evidence]) == [
        {"Source table": "purchase_orders", "Record key": '["PO-test",1]'}
    ]


def test_empty_and_missing_facts():
    assert format_facts(None) == []
    assert format_facts({}) == []
    assert format_facts({"value": None})[0]["Value"] == "Not supplied"
    assert format_facts({"value": ""})[0]["Value"] == "Not supplied"


def test_unknown_record_presentation():
    issue = BusinessIssue(
        "ERROR", "UNKNOWN_PO", "purchase_orders", "missing", "Purchase order does not exist.", ()
    )
    result = DecisionResult(
        DecisionStatus.BLOCKED, "Purchase order not found.", {"po_no": "missing"}, (issue,), ()
    )
    rendered = view(result)
    assert rendered["status"]["label"] == "BLOCKED"
    assert rendered["evidence"] == []
    assert rendered["stocks"] == []
    assert rendered["issues"][0]["Code"] == "UNKNOWN_PO"
    assert "confidence" not in rendered


@pytest.mark.parametrize("source", ["ANSWER_KEY", "README", "answer_key", "arbitrary"])
def test_excluded_evidence_rejected(source):
    evidence = EvidenceRef(source, "synthetic-marker")
    with pytest.raises(ValueError):
        format_evidence([evidence])
    nested = DecisionResult(DecisionStatus.PASS, "", {}, (), (evidence,))
    outer = DecisionResult(DecisionStatus.PASS, "", {"nested": nested}, (), ())
    with pytest.raises(ValueError):
        view(outer)
    issue = BusinessIssue("WARNING", "TEST", "parts", "P-test", "test", (evidence,))
    with pytest.raises(ValueError):
        view([issue])


def test_dataset_health_filters_nonoperational_tables():
    metadata = {
        "source_filename": "fixture.xlsx",
        "sha256": "a" * 64,
        "tables": [
            {"table": "parts", "rows": 2, "columns": ["part_no"]},
            {"table": "ANSWER_KEY", "rows": 999, "columns": ["secret"]},
            {"table": "README", "rows": 999, "columns": ["docs"]},
        ],
        "issues": [],
    }
    rendered = dataset_view(metadata)
    assert rendered["row_count"] == 2
    assert rendered["table_count"] == 1
    assert "ANSWER_KEY" not in repr(rendered)
    assert "README" not in repr(rendered)


def test_nested_po_view_and_child_trace():
    stock = DecisionResult(
        DecisionStatus.WARNING,
        "Shortage",
        {"part_no": "P-test", "requested_qty": 9, "available_qty": 4, "deficit_qty": 5},
        (),
        (),
    )
    dealer = DecisionResult(
        DecisionStatus.BLOCKED,
        "Dealer ineligible",
        {"dealer_id": "D-test", "dealer_status": "Suspended"},
        (),
        (),
    )
    po = DecisionResult(
        DecisionStatus.BLOCKED,
        "PO evaluated",
        {"lines": ({"dealer_evaluation": dealer},), "stock_positions": {"P-test": stock}},
        (),
        (EvidenceRef("purchase_orders", '["PO-test",1]'),),
    )
    rendered = view(po)
    assert rendered["dealers"] == [
        {"Dealer": "D-test", "Source status": "Suspended", "Eligibility": "BLOCKED"}
    ]
    assert rendered["stocks"][0]["deficit_qty"] == 5
    assert any(row["Value"] == "Suspended" for row in rendered["facts"])
    assert any(
        row["Outcome"] == "BLOCKED" and "dealer" in row["Check"] for row in rendered["checks"]
    )


def inv(inventory_id, part_no, warehouse_loc, on_hand, reserved, available, reorder_point):
    return InventoryRecord(
        inventory_id, part_no, warehouse_loc, on_hand, reserved, available, reorder_point,
        "Bin-1", "2026-01-01",
    )


def part(part_no, part_name, category):
    return Part(part_no, part_name, category, 1.0, "EUR", 12, "S-1", "Supplier", "N", "Active", 5)


def test_inventory_dashboard_joins_flags_and_aggregates():
    records = [
        inv("I-1", "P-1", "WH-A", 10, 2, 8, 5),
        inv("I-2", "P-1", "WH-B", 0, 0, -1, 3),
        inv("I-3", "P-2", "WH-A", 4, 1, 3, 5),
    ]
    inventory_result = RepositoryResult(
        records, tuple(EvidenceRef("inventory", r.inventory_id) for r in records),
    )
    parts = [part("P-1", "Widget", "Body"), part("P-2", "Gizmo", "Brake")]
    parts_result = RepositoryResult(parts, tuple(EvidenceRef("parts", p.part_no) for p in parts))
    view = inventory_dashboard_view(inventory_result, parts_result)
    assert view["summary"] == {
        "distinct_parts": 2, "total_on_hand": 14, "total_available": 10, "warehouse_count": 2,
        "below_reorder_count": 2, "negative_available_count": 1,
    }
    assert view["rows"][0]["Part Name"] == "Widget"
    assert view["rows"][0]["Below Reorder Point"] is False
    assert view["rows"][1]["Negative Available"] is True
    assert view["rows"][1]["Below Reorder Point"] is True
    assert view["rows"][2]["Below Reorder Point"] is True
    warehouses = {w["Warehouse"]: w for w in view["warehouses"]}
    assert warehouses["WH-A"] == {"Warehouse": "WH-A", "On Hand": 14, "Available": 11, "Distinct Parts": 2}
    assert warehouses["WH-B"] == {"Warehouse": "WH-B", "On Hand": 0, "Available": -1, "Distinct Parts": 1}
    categories = {c["Category"]: c for c in view["categories"]}
    assert categories["Body"]["Distinct Parts"] == 1
    assert categories["Brake"]["On Hand"] == 4


def test_inventory_dashboard_missing_part_match_is_not_supplied():
    records = [inv("I-1", "P-missing", "WH-A", 1, 0, 1, 0)]
    inventory_result = RepositoryResult(records, (EvidenceRef("inventory", "I-1"),))
    parts_result = RepositoryResult([], ())
    view = inventory_dashboard_view(inventory_result, parts_result)
    assert view["rows"][0]["Part Name"] == "Not supplied"
    assert view["rows"][0]["Category"] == "Not supplied"


@pytest.mark.parametrize("source", ["ANSWER_KEY", "README"])
def test_inventory_dashboard_rejects_excluded_evidence(source):
    inventory_result = RepositoryResult([], (EvidenceRef(source, "x"),))
    parts_result = RepositoryResult([], ())
    with pytest.raises(ValueError):
        inventory_dashboard_view(inventory_result, parts_result)
    inventory_result = RepositoryResult([], ())
    parts_result = RepositoryResult([], (EvidenceRef(source, "x"),))
    with pytest.raises(ValueError):
        inventory_dashboard_view(inventory_result, parts_result)


def test_scan_conversion():
    assert view([])["status"]["label"] == "PASS"
    issue = BusinessIssue(
        "WARNING",
        "NEGATIVE_INVENTORY",
        "inventory",
        "I-test",
        "Negative quantity.",
        (EvidenceRef("inventory", "I-test"),),
    )
    rendered = view([issue])
    assert rendered["status"]["label"] == "WARNING"
    assert len(rendered["issues"]) == 1
    assert rendered["evidence"][0]["Source table"] == "inventory"
