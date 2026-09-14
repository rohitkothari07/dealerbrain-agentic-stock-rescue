"""Deterministic read-only evaluations over repository facts; no transactions."""

from calendar import monthrange
from collections import Counter
from dataclasses import dataclass
from datetime import date
from enum import Enum
from functools import wraps
import logging
import math
from types import MappingProxyType
from typing import Mapping

from models import EvidenceRef
from repositories import Repository

logger = logging.getLogger(__name__)


class DecisionStatus(str, Enum):
    PASS = "PASS"
    WARNING = "WARNING"
    BLOCKED = "BLOCKED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class BusinessIssue:
    severity: str
    code: str
    entity_type: str
    entity_id: str | None
    message: str
    evidence: tuple[EvidenceRef, ...]


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class DecisionResult:
    status: DecisionStatus
    summary: str
    facts: Mapping[str, object]
    issues: tuple[BusinessIssue, ...]
    evidence: tuple[EvidenceRef, ...]

    def __post_init__(self):
        object.__setattr__(self, "facts", _freeze(dict(self.facts)))
        object.__setattr__(self, "issues", tuple(self.issues))
        object.__setattr__(self, "evidence", tuple(self.evidence))


def _evidence(*groups):
    return tuple(dict.fromkeys(item for group in groups for item in group))


def _issue(code, entity_type, entity_id, message, evidence=(), severity="WARNING"):
    return BusinessIssue(severity, code, entity_type, entity_id, message, tuple(evidence))


def _result(summary, facts, issues=(), evidence=(), status=None):
    if status is None:
        status = (
            DecisionStatus.BLOCKED
            if any(i.severity == "ERROR" for i in issues)
            else DecisionStatus.WARNING
            if issues
            else DecisionStatus.PASS
        )
    return DecisionResult(status, summary, facts, tuple(issues), tuple(evidence))


def valid_identifier(value):
    """Check shape only; preserve the actual identifier without trimming or rewriting."""
    return isinstance(value, str) and bool(value.strip())


def _positive_integer(value):
    return type(value) is int and value > 0


def _positive_price(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _logged(function):
    @wraps(function)
    def wrapped(self, entity_id, *args):
        if not valid_identifier(entity_id):
            result = _result(
                "Invalid identifier.",
                {},
                [
                    _issue(
                        "INVALID_INPUT",
                        "input",
                        None,
                        "Identifier must be a non-empty string.",
                        severity="ERROR",
                    )
                ],
            )
        else:
            result = function(self, entity_id, *args)
        logger.info(
            "rule=%s entity=%r status=%s", function.__name__, entity_id, result.status.value
        )
        return result

    return wrapped


class RulesEngine:
    """Evaluate only facts obtained through an injected operational repository."""

    def __init__(self, repository=None):
        self._repository = repository if repository is not None else Repository()

    @_logged
    def evaluate_stock_position(self, part_no: str, requested_qty: int) -> DecisionResult:
        facts = {"part_no": part_no, "requested_qty": requested_qty}
        if not _positive_integer(requested_qty):
            return _result(
                "Requested quantity must be a positive integer.",
                facts,
                [
                    _issue(
                        "INVALID_REQUESTED_QUANTITY",
                        "parts",
                        part_no,
                        "Requested quantity must be a positive integer.",
                        severity="ERROR",
                    )
                ],
            )
        part = self._repository.get_part(part_no)
        if part.data is None:
            return _result(
                "Part not found.",
                facts,
                [
                    _issue(
                        "UNKNOWN_PART", "parts", part_no, "Part does not exist.", severity="ERROR"
                    )
                ],
            )
        inventory = self._repository.get_inventory_for_part(part_no)
        evidence = _evidence(part.evidence, inventory.evidence)
        if not inventory.data:
            return _result(
                "Inventory is unavailable.",
                facts,
                [
                    _issue(
                        "MISSING_INVENTORY",
                        "parts",
                        part_no,
                        "No inventory records; availability cannot be assumed zero.",
                        evidence,
                        "ERROR",
                    )
                ],
                evidence,
            )
        issues = []
        counts = Counter(row.inventory_id for row in inventory.data)
        for row, ref in zip(inventory.data, inventory.evidence):
            if not valid_identifier(row.inventory_id) or counts[row.inventory_id] > 1:
                issues.append(
                    _issue(
                        "AMBIGUOUS_INVENTORY",
                        "inventory",
                        row.inventory_id,
                        "Inventory key is missing or duplicated.",
                        (ref,),
                        "ERROR",
                    )
                )
            if type(row.available_qty) is not int:
                issues.append(
                    _issue(
                        "INVALID_INVENTORY",
                        "inventory",
                        row.inventory_id,
                        "Explicit available_qty is missing or not an integer.",
                        (ref,),
                        "ERROR",
                    )
                )
            elif row.available_qty < 0:
                issues.append(
                    _issue(
                        "NEGATIVE_INVENTORY",
                        "inventory",
                        row.inventory_id,
                        "Negative available_qty is preserved in the total.",
                        (ref,),
                    )
                )
        if any(i.severity == "ERROR" for i in issues):
            return _result("Inventory cannot be totaled reliably.", facts, issues, evidence)
        # The source has explicit available_qty: do not derive or clamp it.
        available = sum(row.available_qty for row in inventory.data)
        deficit = max(requested_qty - available, 0)
        facts.update(available_qty=available, deficit_qty=deficit)
        if deficit:
            issues.append(
                _issue(
                    "STOCK_SHORTAGE",
                    "parts",
                    part_no,
                    "Requested quantity exceeds total available quantity.",
                    evidence,
                )
            )
        return _result(
            "Stock position calculated from explicit availability.", facts, issues, evidence
        )

    @_logged
    def evaluate_dealer_eligibility(self, dealer_id: str) -> DecisionResult:
        result = self._repository.get_dealer(dealer_id)
        facts = {"dealer_id": dealer_id}
        issues = []
        if result.data is None:
            issues.append(
                _issue(
                    "UNKNOWN_DEALER",
                    "dealers",
                    dealer_id,
                    "Dealer does not exist.",
                    severity="ERROR",
                )
            )
        else:
            facts["dealer_status"] = result.data.status
            # Active is the only eligible status present in this source schema.
            if result.data.status != "Active":
                code = (
                    "DEALER_INELIGIBLE"
                    if result.data.status in {"Suspended", "Inactive"}
                    else "UNKNOWN_DEALER_STATUS"
                )
                issues.append(
                    _issue(
                        code,
                        "dealers",
                        dealer_id,
                        "Dealer is not confirmed active for new transactions.",
                        result.evidence,
                        "ERROR",
                    )
                )
        return _result("Dealer eligibility evaluated.", facts, issues, result.evidence)

    @_logged
    def evaluate_part_eligibility(self, part_no: str) -> DecisionResult:
        result = self._repository.get_part(part_no)
        facts = {"part_no": part_no}
        issues = []
        if result.data is None:
            issues.append(
                _issue("UNKNOWN_PART", "parts", part_no, "Part does not exist.", severity="ERROR")
            )
        else:
            facts["part_status"] = result.data.status
            if result.data.status in {"Discontinued", "Inactive"}:
                issues.append(
                    _issue(
                        "PART_INELIGIBLE",
                        "parts",
                        part_no,
                        "Part is not eligible for new ordering actions.",
                        result.evidence,
                        "ERROR",
                    )
                )
            elif result.data.status != "Active":
                issues.append(
                    _issue(
                        "UNKNOWN_PART_STATUS",
                        "parts",
                        part_no,
                        "Part status is unrecognized.",
                        result.evidence,
                    )
                )
        return _result("Part eligibility evaluated.", facts, issues, result.evidence)

    @_logged
    def evaluate_purchase_order(self, po_no: str) -> DecisionResult:
        order = self._repository.get_purchase_order(po_no)
        if not order.data:
            return _result(
                "Purchase order not found.",
                {"po_no": po_no},
                [
                    _issue(
                        "UNKNOWN_PO",
                        "purchase_orders",
                        po_no,
                        "Purchase order does not exist.",
                        severity="ERROR",
                    )
                ],
            )
        issues, lines, children = [], [], []
        totals, invalid_parts = {}, set()
        keys = Counter((row.po_no, row.po_line_no) for row in order.data)
        for row, ref in zip(order.data, order.evidence):
            if keys[(row.po_no, row.po_line_no)] > 1 or row.po_line_no is None:
                issues.append(
                    _issue(
                        "AMBIGUOUS_PO_LINE",
                        "purchase_orders",
                        row.po_no,
                        "PO line key is missing or duplicated.",
                        (ref,),
                        "ERROR",
                    )
                )
                invalid_parts.add(row.part_no)
            dealer = self.evaluate_dealer_eligibility(row.dealer_id)
            part = self.evaluate_part_eligibility(row.part_no)
            children.extend([dealer, part])
            if not _positive_integer(row.order_qty):
                issues.append(
                    _issue(
                        "NON_POSITIVE_QUANTITY",
                        "purchase_orders",
                        po_no,
                        "Order quantity must be a positive integer.",
                        (ref,),
                        "ERROR",
                    )
                )
                invalid_parts.add(row.part_no)
            else:
                totals[row.part_no] = totals.get(row.part_no, 0) + row.order_qty
            if not _positive_price(row.unit_price_eur):
                issues.append(
                    _issue(
                        "NON_POSITIVE_PRICE",
                        "purchase_orders",
                        po_no,
                        "Unit price must be finite and positive.",
                        (ref,),
                        "ERROR",
                    )
                )
            # EUR-named columns justify a quality warning, not a global currency policy.
            if row.currency != "EUR":
                issues.append(
                    _issue(
                        "CURRENCY_MISMATCH",
                        "purchase_orders",
                        po_no,
                        "Currency is missing or differs from EUR-named price columns.",
                        (ref,),
                    )
                )
            lines.append({"po": row, "dealer_evaluation": dealer, "part_evaluation": part})
        stock = {}
        for part_no, qty in totals.items():
            if part_no not in invalid_parts:
                stock[part_no] = self.evaluate_stock_position(part_no, qty)
                children.append(stock[part_no])
        # Aggregate same-part demand across all PO lines before comparing shared stock.
        for child in children:
            issues.extend(child.issues)
        evidence = _evidence(order.evidence, *(child.evidence for child in children))
        return _result(
            "Purchase order evaluated for new ordering eligibility and current stock.",
            {"po_no": po_no, "lines": lines, "stock_positions": stock},
            tuple(dict.fromkeys(issues)),
            evidence,
        )

    @_logged
    def evaluate_claim_consistency(self, claim_id: str) -> DecisionResult:
        source = self._repository.get_claim(claim_id)
        if source.data is None:
            return _result(
                "Claim not found.",
                {"claim_id": claim_id},
                [
                    _issue(
                        "UNKNOWN_CLAIM",
                        "claims",
                        claim_id,
                        "Claim does not exist.",
                        severity="ERROR",
                    )
                ],
            )
        claim = source.data
        issues, shipments = [], []
        evidence = source.evidence
        for identifier, method, code, label in [
            (claim.dealer_id, self._repository.get_dealer, "UNKNOWN_DEALER", "dealer"),
            (claim.part_no, self._repository.get_part, "UNKNOWN_PART", "part"),
        ]:
            referenced = method(identifier) if valid_identifier(identifier) else None
            if referenced is None or referenced.data is None:
                issues.append(
                    _issue(
                        code,
                        "claims",
                        claim_id,
                        f"Claim references a missing {label}.",
                        source.evidence,
                    )
                )
            else:
                evidence = _evidence(evidence, referenced.evidence)
        # Claims has po_no, not shipment_id. Resolve only matching dealer/part PO lines.
        order = (
            self._repository.get_purchase_order(claim.po_no)
            if valid_identifier(claim.po_no)
            else None
        )
        matching = []
        if order is not None:
            evidence = _evidence(evidence, order.evidence)
            matching = [
                row
                for row in order.data
                if row.dealer_id == claim.dealer_id and row.part_no == claim.part_no
            ]
        if not matching:
            issues.append(
                _issue(
                    "MISSING_SHIPMENT_REFERENCE",
                    "claims",
                    claim_id,
                    "No matching PO line resolves a shipment for this claim.",
                    evidence,
                )
            )
        seen = set()
        for row in matching:
            shipment_id = row.shipment_id
            if shipment_id in seen:
                continue
            seen.add(shipment_id)
            result = (
                self._repository.get_shipment(shipment_id)
                if valid_identifier(shipment_id)
                else None
            )
            if result is None or result.data is None:
                issues.append(
                    _issue(
                        "MISSING_SHIPMENT_REFERENCE",
                        "claims",
                        claim_id,
                        "Matching PO line has no existing referenced shipment.",
                        evidence,
                    )
                )
                continue
            shipment = result.data
            shipments.append(shipment)
            evidence = _evidence(evidence, result.evidence)
            if shipment.po_no != claim.po_no or shipment.dealer_id != claim.dealer_id:
                issues.append(
                    _issue(
                        "SHIPMENT_REFERENCE_MISMATCH",
                        "claims",
                        claim_id,
                        "Linked shipment PO/dealer differs from the claim.",
                        evidence,
                    )
                )
            elif claim.reason == "Not Received" and shipment.shipment_status == "Delivered":
                issues.append(
                    _issue(
                        "SHIPMENT_CLAIM_CONTRADICTION",
                        "claims",
                        claim_id,
                        "Not Received claim links to a Delivered shipment; review evidence. "
                        "This does not establish fraud or reject the claim.",
                        evidence,
                    )
                )
        return _result(
            "Claim references and shipment consistency evaluated.",
            {
                "claim": claim,
                "shipments": shipments,
                "contradiction_flags": tuple(
                    i.code for i in issues if i.code == "SHIPMENT_CLAIM_CONTRADICTION"
                ),
            },
            issues,
            evidence,
        )

    @_logged
    def evaluate_warranty(self, claim_id: str) -> DecisionResult:
        source = self._repository.get_claim(claim_id)
        if source.data is None:
            return _result(
                "Claim not found.",
                {"claim_id": claim_id},
                [
                    _issue(
                        "UNKNOWN_CLAIM",
                        "claims",
                        claim_id,
                        "Claim does not exist.",
                        severity="ERROR",
                    )
                ],
            )
        claim = source.data
        part = self._repository.get_part(claim.part_no) if valid_identifier(claim.part_no) else None
        evidence = _evidence(source.evidence, part.evidence if part else ())
        facts = {
            "claim_id": claim_id,
            "purchase_date": claim.purchase_date,
            "claim_date": claim.claim_date,
            "warranty_months": part.data.warranty_months if part and part.data else None,
        }
        try:
            purchased, claimed = (
                date.fromisoformat(claim.purchase_date),
                date.fromisoformat(claim.claim_date),
            )
            months = facts["warranty_months"]
            if type(months) is not int or months < 0:
                raise ValueError("Missing or invalid warranty duration")
            # Calendar months, clamped to the last day of the target month; end date inclusive.
            year, month = divmod(purchased.year * 12 + purchased.month - 1 + months, 12)
            end = date(year, month + 1, min(purchased.day, monthrange(year, month + 1)[1]))
        except (ValueError, TypeError, OverflowError):
            return _result(
                "Warranty window cannot be calculated from available fields.",
                facts,
                [
                    _issue(
                        "WARRANTY_DATA_UNAVAILABLE",
                        "claims",
                        claim_id,
                        "Valid purchase/claim dates and warranty months are required.",
                        evidence,
                    )
                ],
                evidence,
                DecisionStatus.NOT_APPLICABLE,
            )
        if claimed < purchased:
            return _result(
                "Claim date precedes purchase date.",
                facts,
                [
                    _issue(
                        "INVALID_DATE_ORDER",
                        "claims",
                        claim_id,
                        "Claim date is earlier than purchase date.",
                        evidence,
                        "ERROR",
                    )
                ],
                evidence,
            )
        within = claimed <= end
        facts.update(warranty_end_date=end.isoformat(), within_warranty=within)
        issues = (
            []
            if within
            else [
                _issue(
                    "OUTSIDE_WARRANTY",
                    "claims",
                    claim_id,
                    "Claim date falls after the calculated warranty window.",
                    evidence,
                )
            ]
        )
        return _result(
            "Warranty date window calculated; this is not claim approval.", facts, issues, evidence
        )

    def scan_known_data_quality_issues(self) -> list[BusinessIssue]:
        """Scan operational rows using general source-field predicates, without known scenario IDs."""
        issues = []
        inventory = self._repository.list_inventory()
        for row, ref in zip(inventory.data, inventory.evidence):
            if type(row.available_qty) is int and row.available_qty < 0:
                issues.append(
                    _issue(
                        "NEGATIVE_INVENTORY",
                        "inventory",
                        row.inventory_id,
                        "Explicit available_qty is negative.",
                        (ref,),
                    )
                )
            part = self._repository.get_part(row.part_no) if valid_identifier(row.part_no) else None
            if part is None or part.data is None:
                issues.append(
                    _issue(
                        "UNKNOWN_PART",
                        "inventory",
                        row.inventory_id,
                        "Inventory references a missing part.",
                        (ref,),
                    )
                )
        orders = self._repository.list_purchase_orders()
        for row, ref in zip(orders.data, orders.evidence):
            dealer = (
                self._repository.get_dealer(row.dealer_id)
                if valid_identifier(row.dealer_id)
                else None
            )
            part = self._repository.get_part(row.part_no) if valid_identifier(row.part_no) else None
            evidence = _evidence(
                (ref,), dealer.evidence if dealer else (), part.evidence if part else ()
            )
            entity_id = ref.record_key
            for result, code, message in [
                (dealer, "UNKNOWN_DEALER", "Order references a missing dealer."),
                (part, "UNKNOWN_PART", "Order references a missing part."),
            ]:
                if result is None or result.data is None:
                    issues.append(_issue(code, "purchase_orders", entity_id, message, evidence))
            if not _positive_integer(row.order_qty):
                issues.append(
                    _issue(
                        "NON_POSITIVE_QUANTITY",
                        "purchase_orders",
                        entity_id,
                        "Order quantity is missing, invalid, or non-positive.",
                        evidence,
                    )
                )
            if not _positive_price(row.unit_price_eur):
                issues.append(
                    _issue(
                        "NON_POSITIVE_PRICE",
                        "purchase_orders",
                        entity_id,
                        "Unit price is missing, invalid, or non-positive.",
                        evidence,
                    )
                )
            # Actual unfulfilled order statuses; shipped/delivered/cancelled are historical here.
            if row.po_status in {"Open", "Confirmed", "Backordered"}:
                if dealer and dealer.data and dealer.data.status in {"Suspended", "Inactive"}:
                    issues.append(
                        _issue(
                            "SUSPENDED_DEALER_ORDER",
                            "purchase_orders",
                            entity_id,
                            "Unfulfilled order references an ineligible dealer.",
                            evidence,
                        )
                    )
                if part and part.data and part.data.status in {"Discontinued", "Inactive"}:
                    issues.append(
                        _issue(
                            "DISCONTINUED_PART_ORDER",
                            "purchase_orders",
                            entity_id,
                            "Unfulfilled order references an ineligible part.",
                            evidence,
                        )
                    )
        claims = self._repository.list_claims()
        for row, ref in zip(claims.data, claims.evidence):
            if not valid_identifier(row.claim_id):
                issues.append(
                    _issue(
                        "MISSING_CLAIM_ID",
                        "claims",
                        row.claim_id,
                        "Claim identifier is missing.",
                        (ref,),
                    )
                )
            else:
                issues.extend(self.evaluate_claim_consistency(row.claim_id).issues)
        issues = list(dict.fromkeys(issues))
        logger.info(
            "rule=scan_known_data_quality_issues status=%s issues=%d",
            "WARNING" if issues else "PASS",
            len(issues),
        )
        return issues
