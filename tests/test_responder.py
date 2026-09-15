"""Offline explanation boundary checks and one real-data pipeline smoke."""

import json
import socket
from unittest.mock import Mock

import pytest

from intent import ParsedIntent
from llm_client import (
    FakeLLMClient, LLMConfigurationError, LLMDisabledError, LLMProviderError, LLMTimeoutError,
)
from models import EvidenceRef
from responder import generate_response
from router import ToolExecution, execute_intent
from rules import DecisionResult, DecisionStatus


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("No network permitted")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.fixture
def execution():
    return ToolExecution("CHECK_STOCK", "check_stock", DecisionResult(
        DecisionStatus.WARNING, "Stock check", {
            "part_no": "P-10036", "requested_qty": 20, "available_qty": 4, "deficit_qty": 16,
            "api_key": "synthetic-secret", "ANSWER_KEY": "private-answer",
            "database_rows": [{"contact_email": "private@example.test"}],
        }, (), (EvidenceRef("inventory", "I001"), EvidenceRef("ANSWER_KEY", "private-answer")),
    ))


def capture_client(text="Stock is insufficient according to inventory:I001."):
    client = FakeLLMClient(text)
    client.chat = Mock(wraps=client.chat)
    return client


def context_sent(client):
    messages = client.chat.call_args.args[0]
    return json.loads(messages[1]["content"].removeprefix("VERIFIED_CONTEXT="))


def test_success_and_compact_grounding(execution):
    client = capture_client()
    response = generate_response("Check stock", execution, client)
    assert response.generated_by == "llm"
    assert not response.fallback_used
    assert response.evidence_count == 1
    assert "inventory:I001" in response.text
    client.chat.assert_called_once()
    context = context_sent(client)
    assert context["facts"] == {
        "part_no": "P-10036", "requested_qty": 20, "available_qty": 4, "deficit_qty": 16,
    }
    assert context["status"] == "WARNING"
    assert context["evidence"] == [{"source_table": "inventory", "record_key": "I001"}]
    assert client.chat.call_args.kwargs["max_tokens"] == 256
    assert client.chat.call_args.kwargs["temperature"] == 0


@pytest.mark.parametrize("error", [
    LLMDisabledError, LLMConfigurationError, LLMTimeoutError, LLMProviderError,
])
def test_failure_fallback(execution, error):
    client = capture_client()
    client.chat.side_effect = error("Authorization: synthetic-secret")
    response = generate_response("Check stock", execution, client)
    assert response.fallback_used
    assert response.generated_by == "deterministic"
    for fact in ("requested_qty: 20", "available_qty: 4", "deficit_qty: 16", "WARNING"):
        assert fact in response.text
    assert "inventory:I001" in response.text
    assert "synthetic-secret" not in response.text
    assert "Authorization" not in response.text
    client.chat.assert_called_once()


@pytest.mark.parametrize("text", ["", " \n", None])
def test_empty_output_falls_back(execution, text):
    client = capture_client(text)
    assert generate_response("Check stock", execution, client).fallback_used
    client.chat.assert_called_once()


@pytest.mark.parametrize("execution", [
    ToolExecution("UNKNOWN"), ToolExecution("CHECK_PO", error="synthetic-secret"),
    ToolExecution("CHECK_PO", "check_stock", result=object()),
    ToolExecution("CHECK_PO", "check_po"),
])
def test_no_valid_tool_skips_llm(execution):
    client = capture_client()
    response = generate_response("anything", execution, client)
    assert response.fallback_used
    assert response.evidence_count == 0
    assert "synthetic-secret" not in response.text
    client.chat.assert_not_called()


def test_private_and_irrelevant_data_are_not_sent(execution, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "environment-secret")
    client = capture_client("")
    response = generate_response("Authorization: user-secret", execution, client)
    serialized = str(client.chat.call_args) + response.text
    for excluded in (
        "synthetic-secret", "environment-secret", "user-secret", "ANSWER_KEY",
        "private-answer", "database_rows", "private@example.test",
    ):
        assert excluded not in serialized


def test_empty_anomaly_scan():
    client = capture_client("")
    response = generate_response("Scan", ToolExecution("SCAN_ANOMALIES", "scan_anomalies", []),
                                 client)
    assert "scan completed" in response.text
    assert "none reported" in response.text


def test_oversized_context_skips_llm():
    execution = ToolExecution("CHECK_PART", "check_part", DecisionResult(
        DecisionStatus.PASS, "", {"part_no": "P" * 13000}, (), (),
    ))
    client = capture_client()
    assert generate_response("Check part", execution, client).fallback_used
    client.chat.assert_not_called()


def test_real_po_pipeline():
    execution = execute_intent(ParsedIntent("CHECK_PO", po_id="PO-2026-1026"))
    assert execution.error is None
    client = capture_client("")
    response = generate_response("Can this PO be fulfilled?", execution, client)
    context = context_sent(client)
    assert context["status"] == execution.result.status.value
    assert response.evidence_count == len(context["evidence"]) > 0
    stocks = execution.result.facts["stock_positions"]
    assert stocks
    for projected, original in zip(context["facts"]["stock_positions"], stocks.values()):
        for field in ("requested_qty", "available_qty", "deficit_qty"):
            assert projected["facts"][field] == original.facts[field]
            assert f"{field}: {original.facts[field]}" in response.text
    assert response.fallback_used
    client.chat.assert_called_once()
