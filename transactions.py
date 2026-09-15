"""Explicitly confirmed LOCAL POC simulation ledger; never changes operational data."""

from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from config import RUNTIME_DIR, DATABASE_PATH, WORKBOOK_PATH
from fulfillment import FulfillmentAllocation, FulfillmentPlan, build_fulfillment_plan_for_po
from models import EvidenceRef
from repositories import RepositoryError

ACTION_STORE = RUNTIME_DIR / "dealerbrain_actions.sqlite"


@dataclass(frozen=True)
class SimulatedFulfillmentAction:
    action_id: str
    po_id: str
    part_no: str
    requested_qty: int
    planned_qty: int
    remaining_qty: int
    status: str
    created_at: str
    allocations: tuple[FulfillmentAllocation, ...]
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True)
class TransactionResult:
    status: str
    action: SimulatedFulfillmentAction | None = None
    error: str | None = None


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def execute_simulated_fulfillment(po_id, fulfillment_plan, *, confirmed, store_path=ACTION_STORE):
    """Rebuild from the operational PO before opening the separate SQLite ledger."""
    if confirmed is not True:
        return TransactionResult("REJECTED", error="Explicit human confirmation is required.")
    if not isinstance(po_id, str) or not po_id.strip() or type(fulfillment_plan) is not FulfillmentPlan:
        return TransactionResult("REJECTED", error="A PO and deterministic plan are required.")
    try:
        current = build_fulfillment_plan_for_po(po_id)
        # Canonical serialization also distinguishes booleans/floats from integer quantities.
        if (_json(asdict(current)) != _json(asdict(fulfillment_plan))
                or current.status not in {"FULLY_FULFILLABLE", "PARTIALLY_FULFILLABLE"}
                or current.fulfilled_qty <= 0):
            return TransactionResult("REJECTED", error="Plan is stale, altered, or ineligible.")
    except (RepositoryError, TypeError, ValueError):
        return TransactionResult("REJECTED", error="Authoritative revalidation failed.")
    path = Path(store_path).resolve()
    if path in {DATABASE_PATH.resolve(), WORKBOOK_PATH.resolve()} or path.suffix != ".sqlite":
        return TransactionResult("REJECTED", error="A separate local SQLite action store is required.")
    payload = _json({"po_id": po_id, "plan": asdict(current)})
    action_id = "SIM-FUL-" + sha256(payload.encode()).hexdigest()
    action = SimulatedFulfillmentAction(
        action_id, po_id, current.part_no, current.requested_qty, current.fulfilled_qty,
        current.remaining_qty, "POC_SIMULATED", datetime.now(timezone.utc).isoformat(),
        current.allocations, current.evidence,
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=10)) as connection, connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""CREATE TABLE IF NOT EXISTS fulfillment_actions (
                action_id TEXT PRIMARY KEY, po_id TEXT NOT NULL, plan_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status = 'POC_SIMULATED'),
                action_json TEXT NOT NULL, UNIQUE(po_id, plan_json))""")
            connection.execute("""CREATE TABLE IF NOT EXISTS fulfillment_action_allocations (
                action_id TEXT NOT NULL REFERENCES fulfillment_actions(action_id),
                ordinal INTEGER NOT NULL, source_location TEXT, proposed_qty INTEGER NOT NULL
                CHECK(proposed_qty > 0), evidence_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status = 'POC_SIMULATED'),
                PRIMARY KEY(action_id, ordinal))""")
            existing = connection.execute(
                "SELECT action_json FROM fulfillment_actions WHERE action_id = ?", (action_id,),
            ).fetchone()
            if existing:
                data = json.loads(existing[0])
                data["evidence"] = tuple(EvidenceRef(**e) for e in data["evidence"])
                data["allocations"] = tuple(FulfillmentAllocation(
                    a["source_location"], a["available_qty"], a["proposed_qty"],
                    tuple(EvidenceRef(**e) for e in a["evidence"]),
                ) for a in data["allocations"])
                return TransactionResult("ALREADY_EXISTS", SimulatedFulfillmentAction(**data))
            connection.execute("INSERT INTO fulfillment_actions VALUES (?, ?, ?, ?, ?)", (
                action_id, po_id, payload, action.status, _json(asdict(action)),
            ))
            connection.executemany(
                "INSERT INTO fulfillment_action_allocations VALUES (?, ?, ?, ?, ?, ?)",
                [(action_id, i, a.source_location, a.proposed_qty,
                  _json([asdict(e) for e in a.evidence]), "POC_SIMULATED")
                 for i, a in enumerate(action.allocations)],
            )
        return TransactionResult("CREATED", action)
    except (sqlite3.Error, OSError):
        return TransactionResult("REJECTED", error="Local simulation store is unavailable.")
