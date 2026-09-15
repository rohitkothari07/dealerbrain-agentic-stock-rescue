"""Offline adapter tests: HTTP is always intercepted and credentials are synthetic."""

from dataclasses import asdict
from io import BytesIO
import importlib
import json
import socket
import traceback
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest

from config import LLMSettings
import llm_client as llm
from prompts import DEALERBRAIN_SYSTEM_PROMPT

SECRET = "synthetic-unit-test-credential"
MESSAGES = [{"role": "user", "content": "Explain the supplied tool result."}]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import os

    for key in list(os.environ):
        if key.startswith("LLM_"):
            monkeypatch.delenv(key)

    def deny(*args, **kwargs):
        raise AssertionError("External network is forbidden in adapter tests.")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)


@pytest.fixture
def configured(monkeypatch):
    for key, value in {
        "LLM_ENABLED": "true",
        "LLM_API_KEY": SECRET,
        "LLM_BASE_URL": "https://llm.invalid/v1",
        "LLM_MODEL": "test-model",
    }.items():
        monkeypatch.setenv(key, value)
    return llm.LLMClient(tracker=llm.UsageTracker())


def http_response(monkeypatch, data=None, error=None, raw=None):
    if data is None:
        data = {
            "choices": [{"message": {"content": "Grounded explanation."}}],
            "model": "test-model",
            "id": "request-test",
        }
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = raw if raw is not None else json.dumps(data).encode()
    response.headers = {"x-request-id": "header-id"}
    opener = Mock()
    opener.open.side_effect = error
    if error is None:
        opener.open.return_value = response
    factory = Mock(return_value=opener)
    monkeypatch.setattr(llm, "build_opener", factory)
    return opener


def test_disabled_no_credentials(monkeypatch):
    factory = Mock(side_effect=AssertionError("No HTTP expected"))
    monkeypatch.setattr(llm, "build_opener", factory)
    with pytest.raises(llm.LLMDisabledError):
        llm.LLMClient().chat(MESSAGES)
    assert llm.get_llm_status().state == "Disabled"
    factory.assert_not_called()


@pytest.mark.parametrize("missing", ["LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"])
def test_missing_config(configured, monkeypatch, missing):
    monkeypatch.delenv(missing)
    with pytest.raises(llm.LLMConfigurationError):
        configured.chat(MESSAGES)
    assert llm.get_llm_status().state == "Not Configured"


@pytest.mark.parametrize(
    "key,value",
    [
        ("LLM_TIMEOUT_SECONDS", "NaN"),
        ("LLM_TIMEOUT_SECONDS", "0"),
        ("LLM_MAX_OUTPUT_TOKENS", "-1"),
        ("LLM_TEMPERATURE", "inf"),
        ("LLM_BUDGET_USD", "-1"),
        ("LLM_ENABLED", "maybe"),
        ("LLM_BASE_URL", "http://llm.invalid"),
        ("LLM_BASE_URL", "https://user:password@llm.invalid"),
        ("LLM_BASE_URL", "https://llm.invalid?api_key=secret"),
        ("LLM_INPUT_PRICE_PER_MILLION", "1"),
    ],
)
def test_invalid_configuration_is_safe(configured, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(llm.LLMConfigurationError) as error:
        configured.chat(MESSAGES)
    assert SECRET not in str(error.value)
    assert llm.get_llm_status().state == "Not Configured"


def test_fake_determinism():
    fake = llm.FakeLLMClient(
        "Configured mock text", prompt_tokens=5, completion_tokens=2, total_tokens=7
    )
    first, second = fake.chat(MESSAGES), fake.chat(MESSAGES)
    assert first == second
    assert first.text == "Configured mock text"
    assert first.latency_ms == 0
    assert fake.usage.snapshot().request_count == 2
    assert fake.usage.snapshot().total_tokens == 14
    assert fake.usage.snapshot().estimated_spend_usd is None


def test_usage_parsing_and_request_boundary(configured, monkeypatch):
    data = {
        "choices": [{"message": {"content": "Source-backed text"}}],
        "model": "test-model",
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }
    opener = http_response(monkeypatch, data)
    result = configured.chat(MESSAGES)
    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (100, 20, 120)
    assert result.estimated_cost_usd is None
    assert result.request_id == "header-id"
    request = opener.open.call_args.args[0]
    payload = json.loads(request.data)
    assert request.full_url == "https://llm.invalid/v1/chat/completions"
    assert payload["messages"][0]["content"] == DEALERBRAIN_SYSTEM_PROMPT
    assert payload["messages"][1:] == MESSAGES
    assert payload["max_tokens"] == 512
    assert opener.open.call_args.kwargs["timeout"] == 20
    assert not {"tools", "functions"} & set(payload)
    configured.chat(MESSAGES)
    usage = configured.usage.snapshot()
    assert usage.request_count == usage.successful_requests == 2
    assert (
        usage.prompt_tokens == 200 and usage.completion_tokens == 40 and usage.total_tokens == 240
    )
    assert not hasattr(configured.usage, "responses")  # Tracker retains no response text.


def test_absent_usage_stays_unknown(configured, monkeypatch):
    http_response(monkeypatch)
    result = configured.chat(MESSAGES)
    assert result.prompt_tokens is result.completion_tokens is result.total_tokens is None
    assert configured.usage.snapshot().total_tokens is None


def test_partial_usage_is_not_fabricated(configured, monkeypatch):
    http_response(
        monkeypatch,
        {
            "choices": [{"message": {"content": "text"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": True},
        },
    )
    result = configured.chat(MESSAGES)
    assert result.prompt_tokens == 3
    assert result.completion_tokens is None and result.total_tokens is None


@pytest.mark.parametrize(
    "status,error_name",
    [
        (401, "LLMAuthenticationError"),
        (403, "LLMAuthenticationError"),
        (429, "LLMRateLimitError"),
        (500, "LLMProviderError"),
        (302, "LLMProviderError"),
    ],
)
def test_http_failure_mapping(configured, monkeypatch, status, error_name, caplog):
    error = HTTPError("https://llm.invalid/" + SECRET, status, SECRET, {}, BytesIO(SECRET.encode()))
    opener = http_response(monkeypatch, error=error)
    with pytest.raises(getattr(llm, error_name)) as caught:
        configured.chat(MESSAGES)
    assert SECRET not in str(caught.value)
    assert SECRET not in "".join(traceback.format_exception(caught.value))
    assert SECRET not in caplog.text
    assert opener.open.call_count == 1
    assert configured.usage.snapshot().request_count == 1
    assert configured.usage.snapshot().successful_requests == 0
    assert configured.usage.snapshot().estimated_spend_usd is None


@pytest.mark.parametrize(
    "error,error_name",
    [
        (TimeoutError(SECRET), "LLMTimeoutError"),
        (URLError(socket.timeout(SECRET)), "LLMTimeoutError"),
        (URLError(SECRET), "LLMProviderError"),
    ],
)
def test_network_failure_mapping(configured, monkeypatch, error, error_name):
    http_response(monkeypatch, error=error)
    with pytest.raises(getattr(llm, error_name)) as caught:
        configured.chat(MESSAGES)
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": None}}]},
        {"choices": [{"message": {"content": "", "tool_calls": [{"name": "sql"}]}}]},
    ],
)
def test_bad_schema_and_tool_calls_rejected(configured, monkeypatch, data):
    http_response(monkeypatch, data)
    with pytest.raises(llm.LLMProviderError):
        configured.chat(MESSAGES)


def test_invalid_json(configured, monkeypatch):
    http_response(monkeypatch, raw=b"not json")
    with pytest.raises(llm.LLMProviderError):
        configured.chat(MESSAGES)


def test_redirects_never_forward_credentials():
    assert (
        llm._NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid") is None
    )


def test_output_and_input_limits(configured, monkeypatch):
    opener = http_response(monkeypatch)
    for kwargs in [{"max_tokens": 513}, {"max_tokens": True}, {"temperature": float("nan")}]:
        with pytest.raises(llm.LLMConfigurationError):
            configured.chat(MESSAGES, **kwargs)
    for messages in [
        [],
        [{"role": "tool", "content": "x"}],
        [{"role": [], "content": "x"}],
        [{"role": "user", "content": "x" * 32001}],
    ]:
        with pytest.raises(llm.LLMConfigurationError):
            configured.chat(messages)
    opener.open.assert_not_called()


def test_pricing_soft_budget(configured, monkeypatch):
    monkeypatch.setenv("LLM_INPUT_PRICE_PER_MILLION", "1")
    monkeypatch.setenv("LLM_OUTPUT_PRICE_PER_MILLION", "2")
    monkeypatch.setenv("LLM_BUDGET_USD", "0.00001")
    opener = http_response(
        monkeypatch,
        {
            "choices": [{"message": {"content": "text"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    )
    result = configured.chat(MESSAGES)
    assert result.estimated_cost_usd == pytest.approx(0.00002)
    with pytest.raises(llm.LLMBudgetError):
        configured.chat(MESSAGES)
    assert opener.open.call_count == 1


def test_unknown_pricing_does_not_pretend_enforcement(configured, monkeypatch):
    monkeypatch.setenv("LLM_BUDGET_USD", "0")
    http_response(monkeypatch)
    configured.chat(MESSAGES)
    assert configured.usage.snapshot().estimated_spend_usd is None


def test_missing_usage_blocks_further_priced_budget_calls(configured, monkeypatch):
    for key in ["LLM_INPUT_PRICE_PER_MILLION", "LLM_OUTPUT_PRICE_PER_MILLION", "LLM_BUDGET_USD"]:
        monkeypatch.setenv(key, "1")
    opener = http_response(monkeypatch)
    configured.chat(MESSAGES)
    with pytest.raises(llm.LLMBudgetError, match="incomplete"):
        configured.chat(MESSAGES)
    assert opener.open.call_count == 1


def test_zero_known_budget_makes_no_request(configured, monkeypatch):
    monkeypatch.setenv("LLM_INPUT_PRICE_PER_MILLION", "1")
    monkeypatch.setenv("LLM_OUTPUT_PRICE_PER_MILLION", "1")
    monkeypatch.setenv("LLM_BUDGET_USD", "0")
    opener = http_response(monkeypatch)
    with pytest.raises(llm.LLMBudgetError):
        configured.chat(MESSAGES)
    opener.open.assert_not_called()


def test_status_and_repr_never_expose_secrets(configured, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", SECRET)
    settings = LLMSettings.from_environment()
    assert SECRET not in repr(settings)
    status = llm.get_llm_status()
    assert status.state == "Configured"
    assert SECRET not in str(asdict(status))
    assert set(asdict(status)) == {"state", "detail"}


def test_import_performs_no_http(monkeypatch):
    import urllib.request

    factory = Mock(side_effect=AssertionError("Import must not build an HTTP client"))
    monkeypatch.setattr(urllib.request, "build_opener", factory)
    importlib.reload(llm)
    factory.assert_not_called()
