"""Pure presentation tests; no Streamlit automation or external services."""

import pytest

from models import EvidenceRef
from rules import BusinessIssue, DecisionResult, DecisionStatus
from ui_models import dataset_view, format_evidence, format_facts, format_status, result_view


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
