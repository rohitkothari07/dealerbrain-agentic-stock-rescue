"""Read-only warehouse allocation proposals; no stock is reserved or moved."""

from dataclasses import dataclass, replace
from enum import Enum

from models import EvidenceRef
from repositories import Repository
from rules import BusinessIssue, DecisionStatus, RulesEngine


class FulfillmentStatus(str, Enum):
    FULLY_FULFILLABLE = "FULLY_FULFILLABLE"
    PARTIALLY_FULFILLABLE = "PARTIALLY_FULFILLABLE"
    NO_STOCK = "NO_STOCK"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class FulfillmentAllocation:
    source_location: str | None
    available_qty: int
    proposed_qty: int
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True)
class FulfillmentPlan:
    part_no: str | None
    requested_qty: int | None
    network_available_qty: int | None
    fulfilled_qty: int
    remaining_qty: int | None
    allocations: tuple[FulfillmentAllocation, ...]
    status: FulfillmentStatus
    issues: tuple[BusinessIssue, ...]
    evidence: tuple[EvidenceRef, ...]


def _blocked(part_no, quantity, *decisions):
    quantity = quantity if type(quantity) is int and quantity > 0 else None
    return FulfillmentPlan(
        part_no, quantity, None, 0, quantity, (), FulfillmentStatus.BLOCKED,
        tuple(dict.fromkeys(i for d in decisions for i in d.issues)),
        tuple(dict.fromkeys(e for d in decisions for e in d.evidence)),
    )


def build_fulfillment_plan(part_no, requested_qty, *, repository=None):
    repository = repository if repository is not None else Repository()
    engine = RulesEngine(repository)
    stock = engine.evaluate_stock_position(part_no, requested_qty)
    if stock.status == DecisionStatus.BLOCKED:
        return _blocked(part_no, requested_qty, stock)
    part = engine.evaluate_part_eligibility(part_no)
    if part.status == DecisionStatus.BLOCKED:
        return _blocked(part_no, requested_qty, stock, part)
    inventory = repository.get_inventory_for_part(part_no)
    available = stock.facts["available_qty"]
    # Negative source anomalies remain authoritative in the network total. Positive
    # rows cannot justify allocating beyond that net total, even if their sum is larger.
    budget = min(requested_qty, max(available, 0))
    allocations = []
    rows = sorted(zip(inventory.data, inventory.evidence), key=lambda pair: (
        -pair[0].available_qty, pair[0].warehouse_loc or "", pair[0].inventory_id,
    ))
    for row, ref in rows:
        if row.available_qty <= 0 or budget == 0:
            continue
        proposed = min(row.available_qty, budget)
        allocations.append(FulfillmentAllocation(
            row.warehouse_loc, row.available_qty, proposed, (ref,),
        ))
        budget -= proposed
    fulfilled = sum(a.proposed_qty for a in allocations)
    remaining = requested_qty - fulfilled
    status = (
        FulfillmentStatus.FULLY_FULFILLABLE if remaining == 0
        else FulfillmentStatus.PARTIALLY_FULFILLABLE if fulfilled
        else FulfillmentStatus.NO_STOCK
    )
    return FulfillmentPlan(
        part_no, requested_qty, available, fulfilled, remaining, tuple(allocations), status,
        tuple(dict.fromkeys((*stock.issues, *part.issues))),
        tuple(dict.fromkeys((*stock.evidence, *part.evidence, *inventory.evidence))),
    )


def build_fulfillment_plan_for_po(po_id, *, repository=None):
    """Single-part PO plan; the PO engine aggregates demand across same-part lines."""
    repository = repository if repository is not None else Repository()
    decision = RulesEngine(repository).evaluate_purchase_order(po_id)
    positions = decision.facts.get("stock_positions", {})
    part_no, stock = next(iter(positions.items())) if len(positions) == 1 else (None, None)
    quantity = stock.facts.get("requested_qty") if stock else None
    if decision.status == DecisionStatus.BLOCKED:
        return _blocked(part_no, quantity, decision)
    if len(positions) != 1:
        plan = _blocked(None, None, decision)
        issue = BusinessIssue(
            "ERROR", "UNSUPPORTED_PO_SCOPE", "purchase_orders", po_id,
            "A fulfillment plan requires exactly one distinct part in the PO.", decision.evidence,
        )
        return replace(plan, issues=(*plan.issues, issue))
    plan = build_fulfillment_plan(part_no, quantity, repository=repository)
    return replace(
        plan, issues=tuple(dict.fromkeys((*decision.issues, *plan.issues))),
        evidence=tuple(dict.fromkeys((*decision.evidence, *plan.evidence))),
    )
