"""Immutable source records and factual provenance; standard-library types only."""

from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class EvidenceRef:
    source_table: str
    record_key: str


@dataclass(frozen=True)
class RepositoryResult(Generic[T]):
    data: T
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True)
class Dealer:
    """One source row from dealers; nulls and blank strings are preserved."""

    dealer_id: str | None
    dealer_name: str | None
    region: str | None
    country: str | None
    city: str | None
    tier: str | None
    status: str | None
    contact_email: str | None
    credit_limit_eur: int | None
    onboarded_date: str | None


@dataclass(frozen=True)
class Part:
    """One source row from parts; nulls and blank strings are preserved."""

    part_no: str | None
    part_name: str | None
    category: str | None
    unit_price_eur: float | None
    currency: str | None
    warranty_months: int | None
    supplier_id: str | None
    supplier_name: str | None
    hazmat_flag: str | None
    status: str | None
    lead_time_days: int | None


@dataclass(frozen=True)
class PurchaseOrder:
    """One source row from purchase_orders; nulls and blank strings are preserved."""

    po_no: str | None
    po_line_no: int | None
    dealer_id: str | None
    part_no: str | None
    order_qty: int | None
    unit_price_eur: float | None
    line_total_eur: float | None
    order_date: str | None
    requested_delivery_date: str | None
    po_status: str | None
    shipment_id: str | None
    currency: str | None


@dataclass(frozen=True)
class InventoryRecord:
    """One source row from inventory; nulls and blank strings are preserved."""

    inventory_id: str | None
    part_no: str | None
    warehouse_loc: str | None
    on_hand_qty: int | None
    reserved_qty: int | None
    available_qty: int | None
    reorder_point: int | None
    bin_location: str | None
    last_count_date: str | None


@dataclass(frozen=True)
class Shipment:
    """One source row from shipments; nulls and blank strings are preserved."""

    shipment_id: str | None
    po_no: str | None
    dealer_id: str | None
    carrier: str | None
    ship_date: str | None
    est_delivery_date: str | None
    actual_delivery_date: str | None
    shipment_status: str | None
    tracking_no: str | None
    qty: int | None


@dataclass(frozen=True)
class Claim:
    """One source row from claims; nulls and blank strings are preserved."""

    claim_id: str | None
    claim_type: str | None
    dealer_id: str | None
    part_no: str | None
    po_no: str | None
    claim_qty: int | None
    purchase_date: str | None
    claim_date: str | None
    claim_status: str | None
    reason: str | None
    claim_amount_eur: float | None
    resolution_code: str | None


@dataclass(frozen=True)
class KnowledgeRecord:
    """One source row from knowledge; nulls and blank strings are preserved."""

    doc_id: str | None
    doc_type: str | None
    title: str | None
    module: str | None
    error_code: str | None
    summary: str | None
    last_updated: str | None
    owner_team: str | None
