"""Allocation invariants using existing rules and a small read-only repository fake."""

from dataclasses import fields

import pytest

from fulfillment import build_fulfillment_plan, build_fulfillment_plan_for_po
from models import Dealer, EvidenceRef, InventoryRecord, Part, PurchaseOrder, RepositoryResult
from repositories import Repository
from rules import RulesEngine


def record(cls, **values):
    return cls(**{f.name: values.get(f.name) for f in fields(cls)})


class Source:
    def __init__(self, quantities, part_status="Active", dealer_status="Active"):
        self.rows = tuple(record(
            InventoryRecord, inventory_id=f"I{i}", part_no="P", warehouse_loc=f"WH{i}",
            available_qty=qty,
        ) for i, qty in enumerate(quantities))
        self.part_status = part_status
        self.dealer_status = dealer_status

    def get_part(self, key):
        return RepositoryResult(record(Part, part_no=key, status=self.part_status),
                                (EvidenceRef("parts", key),))

    def get_inventory_for_part(self, key):
        return RepositoryResult(list(self.rows), tuple(
            EvidenceRef("inventory", r.inventory_id) for r in self.rows
        ))

    def get_dealer(self, key):
        return RepositoryResult(record(Dealer, dealer_id=key, status=self.dealer_status),
                                (EvidenceRef("dealers", key),))

    def get_purchase_order(self, key):
        return RepositoryResult([record(
            PurchaseOrder, po_no=key, po_line_no=1, dealer_id="D", part_no="P",
            order_qty=10, unit_price_eur=1, currency="EUR",
        )], (EvidenceRef("purchase_orders", key),))


@pytest.mark.parametrize("quantities, requested, status, fulfilled", [
    ([4, 8], 10, "FULLY_FULFILLABLE", 10),
    ([1, 3], 20, "PARTIALLY_FULFILLABLE", 4),
    ([0, 0], 10, "NO_STOCK", 0),
    ([-2, 0, 5], 10, "PARTIALLY_FULFILLABLE", 3),
    ([-5, 2], 10, "NO_STOCK", 0),
])
def test_allocations_and_no_mutation(quantities, requested, status, fulfilled):
    source = Source(quantities)
    before = source.rows
    plan = build_fulfillment_plan("P", requested, repository=source)
    assert plan.status == status
    assert plan.fulfilled_qty == fulfilled
    assert plan.remaining_qty == requested - fulfilled
    assert sum(a.proposed_qty for a in plan.allocations) == fulfilled <= requested
    assert all(0 < a.proposed_qty <= a.available_qty for a in plan.allocations)
    assert plan.network_available_qty == RulesEngine(source).evaluate_stock_position(
        "P", requested
    ).facts["available_qty"]
    assert len({a.evidence[0] for a in plan.allocations}) == len(plan.allocations)
    assert all(a.evidence[0] in plan.evidence for a in plan.allocations)
    assert EvidenceRef("parts", "P") in plan.evidence
    assert source.rows == before


@pytest.mark.parametrize("qty", [0, -1, True, None, "10", 1.5])
def test_invalid_quantity(qty):
    plan = build_fulfillment_plan("P", qty, repository=Source([5]))
    assert plan.status == "BLOCKED"
    assert not plan.allocations


def test_ordering():
    source = Source([4, 8, 4])
    first = build_fulfillment_plan("P", 20, repository=source)
    source.rows = tuple(reversed(source.rows))
    assert build_fulfillment_plan("P", 20, repository=source).allocations == first.allocations
    assert [a.source_location for a in first.allocations] == ["WH1", "WH0", "WH2"]


@pytest.mark.parametrize("status", ["Discontinued", "Inactive"])
def test_ineligible_part(status):
    plan = build_fulfillment_plan("P", 2, repository=Source([10], part_status=status))
    assert plan.status == "BLOCKED"
    assert not plan.allocations


def test_blocked_po():
    plan = build_fulfillment_plan_for_po("PO", repository=Source([10], dealer_status="Suspended"))
    assert plan.status == "BLOCKED"
    assert not plan.allocations
    assert any(i.code == "DEALER_INELIGIBLE" for i in plan.issues)
    assert EvidenceRef("purchase_orders", "PO") in plan.evidence


def test_duplicate_inventory_blocked():
    source = Source([5])
    source.rows = source.rows * 2
    assert build_fulfillment_plan("P", 10, repository=source).status == "BLOCKED"


def test_missing_part(monkeypatch):
    source = Source([5])
    monkeypatch.setattr(source, "get_part", lambda key: RepositoryResult(None, ()))
    plan = build_fulfillment_plan("missing", 2, repository=source)
    assert plan.status == "BLOCKED"
    assert not plan.allocations


def test_real_po():
    repository = Repository()
    decision = RulesEngine(repository).evaluate_purchase_order("PO-2026-1026")
    plan = build_fulfillment_plan_for_po("PO-2026-1026", repository=repository)
    stock = decision.facts["stock_positions"][plan.part_no]
    assert plan.requested_qty == stock.facts["requested_qty"]
    assert plan.network_available_qty == stock.facts["available_qty"]
    assert plan.fulfilled_qty == min(plan.requested_qty, max(plan.network_available_qty, 0))
    assert plan.remaining_qty == stock.facts["deficit_qty"]
    assert set(decision.evidence) <= set(plan.evidence)
    rows = repository.get_inventory_for_part(plan.part_no)
    for allocation in plan.allocations:
        row = next(r for r in rows.data if r.inventory_id == allocation.evidence[0].record_key)
        assert allocation.source_location == row.warehouse_loc
        assert allocation.proposed_qty <= row.available_qty
    assert repository.get_inventory_for_part(plan.part_no) == rows
