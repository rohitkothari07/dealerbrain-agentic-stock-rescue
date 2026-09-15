"""Offline Knowledge Q&A integration with real operational retrieval."""

import json
import socket
from unittest.mock import Mock

import pytest

from intent import ParsedIntent, parse_intent
from llm_client import FakeLLMClient, LLMDisabledError, LLMResponse, LLMStatus
from responder import generate_response
from router import execute_intent
import tools
import ui


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Network forbidden")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.mark.parametrize("question,key", [
    ("What is the procedure for a suspended dealer?", "KB-009"),
    ("What should we do with negative available stock?", "KB-007"),
    ("What does the guidance say about hazmat shipping?", "KB-017"),
])
def test_pipeline(question, key, monkeypatch):
    monkeypatch.setattr(ui, "get_llm_status", lambda: LLMStatus("Configured", ""))
    client = FakeLLMClient()
    client.chat = Mock(side_effect=[
        LLMResponse('{"intent":"SEARCH_KNOWLEDGE"}', "fake"), LLMResponse("", "fake"),
    ])
    result = ui.run_copilot(question, client)
    assert result["intent"] == "SEARCH_KNOWLEDGE"
    assert client.chat.call_count == 2
    records = result["view"]["knowledge"]
    assert records[0]["doc_id"] == key
    context = json.loads(client.chat.call_args.args[0][1]["content"].removeprefix("VERIFIED_CONTEXT="))
    assert context["facts"]["question"] == question
    assert context["facts"]["records"] == records
    assert all(e["source_table"] == "knowledge" for e in context["evidence"])
    assert all(r["summary"] in result["message"] for r in records)
    assert "confidence" not in str(result["view"]).lower()
    assert result["plan"] is None


def test_query_and_router(monkeypatch):
    query = "  What is the policy?  "
    parsed = parse_intent(query, FakeLLMClient('{"intent":"SEARCH_KNOWLEDGE"}'))
    assert parsed.query == query
    search = Mock(return_value=object())
    monkeypatch.setattr(tools, "search_knowledge", search)
    execute_intent(parsed)
    search.assert_called_once_with(query, 3)
    search.reset_mock()
    assert execute_intent(ParsedIntent("SEARCH_KNOWLEDGE")).error
    search.assert_not_called()


def test_no_match_skips_answer():
    execution = execute_intent(ParsedIntent("SEARCH_KNOWLEDGE", query="zxqvblorp"))
    client = FakeLLMClient()
    response = generate_response("zxqvblorp", execution, client)
    assert "couldn't find supporting guidance" in response.text
    assert client.usage.snapshot().request_count == 0


def test_disabled_fallback_is_source_only():
    execution = execute_intent(ParsedIntent("SEARCH_KNOWLEDGE", query="hazmat shipping"))
    client = FakeLLMClient()
    client.chat = Mock(side_effect=LLMDisabledError("disabled"))
    response = generate_response("hazmat shipping", execution, client)
    assert response.fallback_used
    for match in execution.result.matches:
        assert match.record.summary in response.text
    assert "24 hours" not in response.text
    assert "ANSWER_KEY" not in response.text
