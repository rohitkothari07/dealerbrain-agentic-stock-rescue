"""All simulated writes use pytest temporary SQLite paths."""

from dataclasses import replace
import socket
import sqlite3

import pytest

from fulfillment import build_fulfillment_plan_for_po
from repositories import Repository
import transactions
from transactions import execute_simulated_fulfillment


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    def blocked(*args, **kwargs):
        pytest.fail("No network calls allowed")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    original = transactions.sqlite3.connect

    def temporary_only(path, **kwargs):
        assert str(path).startswith(str(tmp_path))
        return original(path, **kwargs)
    monkeypatch.setattr(transactions.sqlite3, "connect", temporary_only)


@pytest.fixture
def plan():
    return build_fulfillment_plan_for_po("PO-2026-1026")


@pytest.mark.parametrize("confirmed", [False, None, 1, "true"])
def test_explicit_confirmation_only(tmp_path, plan, confirmed):
    path = tmp_path / "actions.sqlite"
    result = execute_simulated_fulfillment("PO-2026-1026", plan, confirmed=confirmed, store_path=path)
    assert result.status == "REJECTED"
    assert not path.exists()


def test_real_action_and_idempotency(tmp_path, plan):
    path = tmp_path / "actions.sqlite"
    repo = Repository()
    inventory = repo.get_inventory_for_part(plan.part_no)
    po = repo.get_purchase_order("PO-2026-1026")
    assert execute_simulated_fulfillment(
        "PO-2026-1026", plan, confirmed=False, store_path=path,
    ).status == "REJECTED"
    assert not path.exists()
    result = execute_simulated_fulfillment("PO-2026-1026", plan, confirmed=True, store_path=path)
    assert result.status == "CREATED"
    assert result.action.status == "POC_SIMULATED"
    assert result.action.action_id.startswith("SIM-FUL-")
    assert result.action.planned_qty == plan.fulfilled_qty
    assert result.action.remaining_qty == plan.remaining_qty
    assert result.action.allocations == plan.allocations
    again = execute_simulated_fulfillment("PO-2026-1026", plan, confirmed=True, store_path=path)
    assert again.status == "ALREADY_EXISTS"
    assert again.action == result.action
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM fulfillment_actions").fetchone()[0] == 1
        rows = connection.execute(
            "SELECT source_location, proposed_qty, status FROM fulfillment_action_allocations ORDER BY ordinal"
        ).fetchall()
        assert rows == [(a.source_location, a.proposed_qty, "POC_SIMULATED") for a in plan.allocations]
    assert repo.get_inventory_for_part(plan.part_no) == inventory
    assert repo.get_purchase_order("PO-2026-1026") == po


@pytest.mark.parametrize("quantity", [0, -1, 999, True])
def test_tampered_allocation(tmp_path, plan, quantity):
    tampered = replace(plan, allocations=(replace(plan.allocations[0], proposed_qty=quantity),))
    path = tmp_path / "actions.sqlite"
    assert execute_simulated_fulfillment(
        "PO-2026-1026", tampered, confirmed=True, store_path=path,
    ).status == "REJECTED"
    assert not path.exists()


@pytest.mark.parametrize("change", [{"fulfilled_qty": 0}, {"requested_qty": 999},
                                    {"part_no": "tampered"}, {"evidence": ()}])
def test_tampered_plan(tmp_path, plan, change):
    path = tmp_path / "actions.sqlite"
    assert execute_simulated_fulfillment(
        "PO-2026-1026", replace(plan, **change), confirmed=True, store_path=path,
    ).status == "REJECTED"
    assert not path.exists()


@pytest.mark.parametrize("kind", ["part", "dealer"])
def test_changed_governance(tmp_path, plan, monkeypatch, kind):
    method = "get_part" if kind == "part" else "get_dealer"
    original = getattr(Repository, method)

    def changed(self, key):
        record = original(self, key)
        return replace(record, data=replace(record.data, status=(
            "Discontinued" if kind == "part" else "Suspended"
        )))
    monkeypatch.setattr(Repository, method, changed)
    path = tmp_path / "actions.sqlite"
    assert execute_simulated_fulfillment(
        "PO-2026-1026", plan, confirmed=True, store_path=path,
    ).status == "REJECTED"
    assert not path.exists()
