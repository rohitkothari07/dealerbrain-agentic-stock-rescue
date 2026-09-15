"""Readiness uses real deterministic APIs, never model calls or action writes."""

import socket
from unittest.mock import Mock

import pytest

from llm_client import LLMClient
from repositories import Repository
from scripts import demo_smoke
import tools
import transactions


@pytest.fixture(autouse=True)
def guard_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("LLM/network/SQLite calls forbidden")
    monkeypatch.setenv("LLM_ENABLED", "false")
    monkeypatch.setattr(LLMClient, "chat", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(transactions.sqlite3, "connect", forbidden)
    original = Repository._lookup

    def operational_only(self, operation, key=None):
        assert operation in {"list_inventory", "list_purchase_orders", "list_knowledge",
                             "get_purchase_order", "get_part", "get_dealer",
                             "get_inventory_for_part", "list_claims", "get_claim", "get_shipment"}
        assert key not in {"ANSWER_KEY", "README"}
        return original(self, operation, key)
    monkeypatch.setattr(Repository, "_lookup", operational_only)


def test_all_checks_and_repeated_zero_writes(monkeypatch, capsys):
    original = transactions.execute_simulated_fulfillment
    calls = Mock(wraps=original)
    monkeypatch.setattr(transactions, "execute_simulated_fulfillment", calls)
    assert demo_smoke.main() == 0
    assert demo_smoke.main() == 0
    assert "7/7 checks passed" in capsys.readouterr().out
    assert calls.call_count == 2
    assert all(c.kwargs["confirmed"] is False for c in calls.call_args_list)
    assert all(not c.kwargs["store_path"].exists() for c in calls.call_args_list)


def test_named_failure(monkeypatch, capsys):
    monkeypatch.setattr(tools, "check_po", Mock(side_effect=RuntimeError("private detail")))
    assert demo_smoke.main() == 1
    output = capsys.readouterr().out
    assert "FAIL  Governance" in output
    assert "private detail" not in output
    assert "DEMO READY: FAIL" in output
