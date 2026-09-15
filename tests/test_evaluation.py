"""Scripted routing only; tests never contact a provider or create actions."""

from dataclasses import asdict, replace
import json
import socket
from unittest.mock import Mock

import pytest

from evaluation import GOLDEN_CASES, evaluate_case, evaluate_cases
from llm_client import FakeLLMClient
from models import EvidenceRef
import transactions


@pytest.fixture(autouse=True)
def no_external_effects(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Network or transaction forbidden")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(transactions, "execute_simulated_fulfillment", blocked)


def client_for(case):
    return FakeLLMClient(json.dumps(asdict(case.expected)))


def test_all_goldens():
    client = FakeLLMClient()
    client.chat = Mock(side_effect=[client_for(c)._response for c in GOLDEN_CASES])
    results = evaluate_cases(GOLDEN_CASES, client)
    assert len(results) == 10
    assert all(r.passed for r in results)
    assert client.chat.call_count == 10


def test_wrong_intent():
    r = evaluate_case(GOLDEN_CASES[0], FakeLLMClient('{"intent":"UNKNOWN"}'))
    assert not r.intent_correct and not r.passed
    assert r.deterministic_result_correct


def test_wrong_parameters():
    r = evaluate_case(GOLDEN_CASES[0], FakeLLMClient(
        '{"intent":"PLAN_FULFILLMENT","po_id":"wrong"}'
    ))
    assert r.intent_correct and not r.parameters_correct and not r.passed


def test_wrong_truth():
    case = replace(GOLDEN_CASES[0], facts=(("planned_fulfillment_qty", 999),))
    assert not evaluate_case(case, client_for(case)).deterministic_result_correct


def test_wrong_evidence():
    case = replace(GOLDEN_CASES[2], evidence=(EvidenceRef("knowledge", "missing"),))
    r = evaluate_case(case, client_for(case))
    assert r.deterministic_result_correct and not r.evidence_correct and not r.passed
