"""Supported operational read API: fixed queries, source models, and row evidence."""

import json
import logging
from pathlib import Path
from types import MappingProxyType

import duckdb

from config import DATABASE_PATH
from database import get_connection
from models import (
    Claim,
    Dealer,
    EvidenceRef,
    InventoryRecord,
    KnowledgeRecord,
    Part,
    PurchaseOrder,
    RepositoryResult,
    Shipment,
)

logger = logging.getLogger(__name__)


class RepositoryError(Exception):
    """Database/schema failure or ambiguous single-record lookup, not a missing record."""


# Internal operation allowlist. Neither SQL nor table/column names come from callers.
_OPERATIONS = MappingProxyType(
    {
        "get_dealer": (
            "dealers",
            Dealer,
            ("dealer_id",),
            False,
            """
        SELECT dealer_id, dealer_name, region, country, city, tier, status, contact_email,
            credit_limit_eur, onboarded_date
        FROM dealers WHERE dealer_id = ?
        ORDER BY dealer_id, dealer_name, region, country, city, tier, status, contact_email,
            credit_limit_eur, onboarded_date
        """,
        ),
        "get_part": (
            "parts",
            Part,
            ("part_no",),
            False,
            """
        SELECT part_no, part_name, category, unit_price_eur, currency, warranty_months,
            supplier_id, supplier_name, hazmat_flag, status, lead_time_days
        FROM parts WHERE part_no = ?
        ORDER BY part_no, part_name, category, unit_price_eur, currency, warranty_months,
            supplier_id, supplier_name, hazmat_flag, status, lead_time_days
        """,
        ),
        "get_purchase_order": (
            "purchase_orders",
            PurchaseOrder,
            ("po_no", "po_line_no"),
            True,
            """
        SELECT po_no, po_line_no, dealer_id, part_no, order_qty, unit_price_eur, line_total_eur,
            order_date, requested_delivery_date, po_status, shipment_id, currency
        FROM purchase_orders WHERE po_no = ?
        ORDER BY po_no, po_line_no, dealer_id, part_no, order_qty, unit_price_eur,
            line_total_eur, order_date, requested_delivery_date, po_status, shipment_id,
            currency
        """,
        ),
        "get_shipment": (
            "shipments",
            Shipment,
            ("shipment_id",),
            False,
            """
        SELECT shipment_id, po_no, dealer_id, carrier, ship_date, est_delivery_date,
            actual_delivery_date, shipment_status, tracking_no, qty
        FROM shipments WHERE shipment_id = ?
        ORDER BY shipment_id, po_no, dealer_id, carrier, ship_date, est_delivery_date,
            actual_delivery_date, shipment_status, tracking_no, qty
        """,
        ),
        "get_claim": (
            "claims",
            Claim,
            ("claim_id",),
            False,
            """
        SELECT claim_id, claim_type, dealer_id, part_no, po_no, claim_qty, purchase_date,
            claim_date, claim_status, reason, claim_amount_eur, resolution_code
        FROM claims WHERE claim_id = ?
        ORDER BY claim_id, claim_type, dealer_id, part_no, po_no, claim_qty, purchase_date,
            claim_date, claim_status, reason, claim_amount_eur, resolution_code
        """,
        ),
        "get_inventory_for_part": (
            "inventory",
            InventoryRecord,
            ("inventory_id",),
            True,
            """
        SELECT inventory_id, part_no, warehouse_loc, on_hand_qty, reserved_qty, available_qty,
            reorder_point, bin_location, last_count_date
        FROM inventory WHERE part_no = ?
        ORDER BY inventory_id, part_no, warehouse_loc, on_hand_qty, reserved_qty, available_qty,
            reorder_point, bin_location, last_count_date
        """,
        ),
        "get_inventory_for_location": (
            "inventory",
            InventoryRecord,
            ("inventory_id",),
            True,
            """
        SELECT inventory_id, part_no, warehouse_loc, on_hand_qty, reserved_qty, available_qty,
            reorder_point, bin_location, last_count_date
        FROM inventory WHERE warehouse_loc = ?
        ORDER BY inventory_id, part_no, warehouse_loc, on_hand_qty, reserved_qty, available_qty,
            reorder_point, bin_location, last_count_date
        """,
        ),
        "get_knowledge_record": (
            "knowledge",
            KnowledgeRecord,
            ("doc_id",),
            False,
            """
        SELECT doc_id, doc_type, title, module, error_code, summary, last_updated, owner_team
        FROM knowledge WHERE doc_id = ?
        ORDER BY doc_id, doc_type, title, module, error_code, summary, last_updated, owner_team
        """,
        ),
        "list_claims_for_dealer": (
            "claims",
            Claim,
            ("claim_id",),
            True,
            """
        SELECT claim_id, claim_type, dealer_id, part_no, po_no, claim_qty, purchase_date,
            claim_date, claim_status, reason, claim_amount_eur, resolution_code
        FROM claims WHERE dealer_id = ?
        ORDER BY claim_id, claim_type, dealer_id, part_no, po_no, claim_qty, purchase_date,
            claim_date, claim_status, reason, claim_amount_eur, resolution_code
        """,
        ),
        "list_purchase_orders_for_dealer": (
            "purchase_orders",
            PurchaseOrder,
            ("po_no", "po_line_no"),
            True,
            """
        SELECT po_no, po_line_no, dealer_id, part_no, order_qty, unit_price_eur, line_total_eur,
            order_date, requested_delivery_date, po_status, shipment_id, currency
        FROM purchase_orders WHERE dealer_id = ?
        ORDER BY po_no, po_line_no, dealer_id, part_no, order_qty, unit_price_eur,
            line_total_eur, order_date, requested_delivery_date, po_status, shipment_id,
            currency
        """,
        ),
        "list_inventory": (
            "inventory",
            InventoryRecord,
            ("inventory_id",),
            True,
            """
        SELECT inventory_id, part_no, warehouse_loc, on_hand_qty, reserved_qty, available_qty,
            reorder_point, bin_location, last_count_date
        FROM inventory
        ORDER BY inventory_id, part_no, warehouse_loc, on_hand_qty, reserved_qty, available_qty,
            reorder_point, bin_location, last_count_date
        """,
        ),
        "list_purchase_orders": (
            "purchase_orders",
            PurchaseOrder,
            ("po_no", "po_line_no"),
            True,
            """
        SELECT po_no, po_line_no, dealer_id, part_no, order_qty, unit_price_eur, line_total_eur,
            order_date, requested_delivery_date, po_status, shipment_id, currency
        FROM purchase_orders
        ORDER BY po_no, po_line_no, dealer_id, part_no, order_qty, unit_price_eur,
            line_total_eur, order_date, requested_delivery_date, po_status, shipment_id,
            currency
        """,
        ),
        "list_claims": (
            "claims",
            Claim,
            ("claim_id",),
            True,
            """
        SELECT claim_id, claim_type, dealer_id, part_no, po_no, claim_qty, purchase_date,
            claim_date, claim_status, reason, claim_amount_eur, resolution_code
        FROM claims
        ORDER BY claim_id, claim_type, dealer_id, part_no, po_no, claim_qty, purchase_date,
            claim_date, claim_status, reason, claim_amount_eur, resolution_code
        """,
        ),
    }
)


class Repository:
    """Read-only exact lookups; use this API for future tools and orchestration."""

    def __init__(self, database_path: Path = DATABASE_PATH):
        self._database_path = Path(database_path)

    def _lookup(self, operation: str, key: str | None = None):
        if operation not in _OPERATIONS:
            raise ValueError("Unsupported repository operation.")
        listing = operation in {"list_inventory", "list_purchase_orders", "list_claims"}
        if not listing and not isinstance(key, str):
            raise TypeError("Lookup identifiers must be strings.")
        table, model, key_columns, many, sql = _OPERATIONS[operation]
        if not self._database_path.is_file():
            raise RepositoryError(
                f"Database not initialized at {self._database_path.resolve()}. "
                "Run python data_loader.py first."
            )
        try:
            with get_connection(self._database_path, read_only=True) as connection:
                rows = connection.execute(sql, [] if listing else [key]).fetchall()
        except (duckdb.Error, OSError) as exc:
            raise RepositoryError(
                f"{operation} could not read the initialized dataset. "
                "Check the database path, schema, and access permissions."
            ) from exc
        if not many and len(rows) > 1:
            raise RepositoryError(
                f"{operation}: duplicate business key; single record is ambiguous."
            )
        records = [model(*row) for row in rows]
        evidence = []
        for record in records:
            values = [getattr(record, column) for column in key_columns]
            # JSON preserves composite-key boundaries and nulls without delimiter ambiguity.
            record_key = (
                values[0]
                if len(values) == 1 and isinstance(values[0], str)
                else json.dumps(values, separators=(",", ":"))
            )
            evidence.append(EvidenceRef(table, record_key))
        logger.info(
            "operation=%s table=%s status=%s rows=%d",
            operation,
            table,
            "found" if rows else "not-found",
            len(rows),
        )
        data = records if many else (records[0] if records else None)
        return RepositoryResult(data, tuple(evidence))

    def get_dealer(self, dealer_id: str) -> RepositoryResult[Dealer | None]:
        """Return the matching dealers record or None, with source evidence."""
        return self._lookup("get_dealer", dealer_id)

    def get_part(self, part_no: str) -> RepositoryResult[Part | None]:
        """Return the matching parts record or None, with source evidence."""
        return self._lookup("get_part", part_no)

    def get_purchase_order(self, po_no: str) -> RepositoryResult[list[PurchaseOrder]]:
        """Return all matching purchase_orders rows, with source evidence."""
        return self._lookup("get_purchase_order", po_no)

    def get_shipment(self, shipment_id: str) -> RepositoryResult[Shipment | None]:
        """Return the matching shipments record or None, with source evidence."""
        return self._lookup("get_shipment", shipment_id)

    def get_claim(self, claim_id: str) -> RepositoryResult[Claim | None]:
        """Return the matching claims record or None, with source evidence."""
        return self._lookup("get_claim", claim_id)

    def get_inventory_for_part(self, part_no: str) -> RepositoryResult[list[InventoryRecord]]:
        """Return all matching inventory rows, with source evidence."""
        return self._lookup("get_inventory_for_part", part_no)

    def get_inventory_for_location(
        self, warehouse_loc: str
    ) -> RepositoryResult[list[InventoryRecord]]:
        """Return all matching inventory rows, with source evidence."""
        return self._lookup("get_inventory_for_location", warehouse_loc)

    def get_knowledge_record(self, doc_id: str) -> RepositoryResult[KnowledgeRecord | None]:
        """Return the matching knowledge record or None, with source evidence."""
        return self._lookup("get_knowledge_record", doc_id)

    def list_claims_for_dealer(self, dealer_id: str) -> RepositoryResult[list[Claim]]:
        """Return all matching claims rows, with source evidence."""
        return self._lookup("list_claims_for_dealer", dealer_id)

    def list_purchase_orders_for_dealer(
        self, dealer_id: str
    ) -> RepositoryResult[list[PurchaseOrder]]:
        """Return all matching purchase_orders rows, with source evidence."""
        return self._lookup("list_purchase_orders_for_dealer", dealer_id)

    def list_inventory(self) -> RepositoryResult[list[InventoryRecord]]:
        """Return operational rows in stable order for deterministic quality scans."""
        return self._lookup("list_inventory")

    def list_purchase_orders(self) -> RepositoryResult[list[PurchaseOrder]]:
        """Return operational rows in stable order for deterministic quality scans."""
        return self._lookup("list_purchase_orders")

    def list_claims(self) -> RepositoryResult[list[Claim]]:
        """Return operational rows in stable order for deterministic quality scans."""
        return self._lookup("list_claims")
