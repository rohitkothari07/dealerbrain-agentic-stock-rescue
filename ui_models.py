"""Pure presentation adapters for deterministic decisions and operational evidence."""

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum

from data_validation import OPERATIONAL_SHEETS
from models import EvidenceRef
from rules import BusinessIssue, DecisionResult, DecisionStatus

OPERATIONAL_TABLES = frozenset(OPERATIONAL_SHEETS.values())
STATUS_STYLES = {
    "PASS": "success",
    "WARNING": "warning",
    "BLOCKED": "error",
    "NOT_APPLICABLE": "info",
}


def format_status(status):
    value = status.value if isinstance(status, DecisionStatus) else status
    if value not in STATUS_STYLES:
        raise ValueError("Unsupported decision status.")
    return {"label": value, "style": STATUS_STYLES[value]}


def _validate_sources(value):
    if isinstance(value, EvidenceRef):
        if value.source_table not in OPERATIONAL_TABLES:
            raise ValueError("Result contains a non-operational evidence source.")
    elif is_dataclass(value):
        for field in fields(value):
            _validate_sources(getattr(value, field.name))
    elif isinstance(value, Mapping):
        for item in value.values():
            _validate_sources(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_sources(item)


def format_evidence(evidence):
    _validate_sources(evidence)
    return [
        {"Source table": item.source_table, "Record key": item.record_key}
        for item in dict.fromkeys(evidence)
    ]


def _text(value):
    if value is None or value == "":
        return "Not supplied"
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def format_facts(facts):
    """Flatten immutable facts without deriving new business values."""
    rows = []

    def visit(value, path):
        if isinstance(value, DecisionResult):
            visit(value.status, path + ("Status",))
            visit(value.facts, path)
        elif is_dataclass(value):
            for field in fields(value):
                visit(getattr(value, field.name), path + (field.name.replace("_", " "),))
        elif isinstance(value, Mapping):
            for key, item in value.items():
                visit(item, path + (str(key).replace("_", " "),))
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value, 1):
                visit(item, path + (str(index),))
        else:
            rows.append({"Fact": " / ".join(path), "Value": _text(value)})

    _validate_sources(facts)
    visit(facts or {}, ())
    return rows


def result_view(result, *, action, tool, check):
    """Convert a tool result to display data; reject excluded provenance before rendering."""
    _validate_sources(result)
    if isinstance(result, DecisionResult):
        status, summary, facts = result.status, result.summary, result.facts
        issues, evidence = result.issues, result.evidence
    elif isinstance(result, list) and all(isinstance(i, BusinessIssue) for i in result):
        issues = result
        # Scan completion presentation only; individual issue severities remain unchanged.
        status = DecisionStatus.WARNING if issues else DecisionStatus.PASS
        summary = f"Operational scan completed: {len(issues)} issue(s) reported."
        facts = {"issues_reported": len(issues)}
        evidence = tuple(e for issue in issues for e in issue.evidence)
    else:
        raise ValueError("Unsupported tool result.")
    checks = [{"Check": check, "Outcome": format_status(status)["label"]}]

    def child_checks(value, label):
        if isinstance(value, DecisionResult):
            checks.append({"Check": label, "Outcome": value.status.value})
        elif isinstance(value, Mapping):
            for key, item in value.items():
                child_checks(item, f"{label} / {str(key).replace('_', ' ')}".strip(" /"))
        elif isinstance(value, (tuple, list)):
            for index, item in enumerate(value, 1):
                child_checks(item, f"{label} {index}")

    child_checks(facts, "")
    stock_results = []
    if "available_qty" in facts and "requested_qty" in facts:
        stock_results.append(facts)
    for child in facts.get("stock_positions", {}).values():
        if "available_qty" in child.facts:
            stock_results.append(child.facts)
    return {
        "action": action,
        "tool": tool,
        "status": format_status(status),
        "summary": summary,
        "facts": format_facts(facts),
        "checks": checks,
        "dealers": [
            {
                "Dealer": line["dealer_evaluation"].facts.get("dealer_id"),
                "Source status": line["dealer_evaluation"].facts.get(
                    "dealer_status", "Not supplied"
                ),
                "Eligibility": line["dealer_evaluation"].status.value,
            }
            for line in facts.get("lines", ())
            if "dealer_evaluation" in line
        ],
        "stocks": [
            {
                key: value.get(key)
                for key in ("part_no", "requested_qty", "available_qty", "deficit_qty")
            }
            for value in stock_results
        ],
        "issues": [
            {"Code": i.code, "Severity": i.severity, "Entity": i.entity_id, "Message": i.message}
            for i in issues
        ],
        "evidence": format_evidence(evidence),
    }


def dataset_view(metadata):
    """Only operational table summaries are eligible for Dataset Health."""
    tables = [t for t in metadata["tables"] if t["table"] in OPERATIONAL_TABLES]
    issues = [i for i in metadata["issues"] if i["table"] in OPERATIONAL_TABLES]
    return {
        "source": metadata["source_filename"],
        "fingerprint": metadata["sha256"][:16] + "…",
        "table_count": len(tables),
        "row_count": sum(t["rows"] for t in tables),
        "errors": sum(i["severity"] == "ERROR" for i in issues),
        "warnings": sum(i["severity"] == "WARNING" for i in issues),
        "tables": [
            {
                "Table": t["table"],
                "Rows": t["rows"],
                "Columns": len(t["columns"]),
                "Status": "Warnings"
                if any(i["table"] == t["table"] and i["severity"] == "WARNING" for i in issues)
                else "Ready",
            }
            for t in tables
        ],
    }
