"""Local loopback URL compatibility without provider calls."""

from dataclasses import replace

import pytest

from config import LLMSettings


@pytest.fixture
def settings():
    return LLMSettings(enabled=True, api_key="synthetic-test-key", model="test-model",
                       base_url="https://valid-provider.example/v1")


@pytest.mark.parametrize("url", [
    "https://valid-provider.example/v1",
    "http://localhost:11434/v1",
    "http://127.0.0.1:11434/v1",
    "http://[::1]:11434/v1",
])
def test_accepted_urls(settings, url):
    replace(settings, base_url=url).validate()


@pytest.mark.parametrize("url", [
    "http://example.com/v1", "http://192.168.1.10:11434/v1",
    "http://127.0.0.2/v1", "http://localhost.example/v1", "http://localhost./v1",
    "http://[::ffff:127.0.0.1]/v1", "http://user:password@localhost/v1",
    "https://user:password@valid-provider.example/v1", "http://@localhost/v1",
    "http://localhost/v1?key=value", "http://localhost/v1#fragment",
    "https://valid-provider.example/v1?key=value", "https://valid-provider.example/v1#fragment",
    "localhost:11434/v1", "http:///v1", "http://[::1/v1", "http://localhost:bad/v1",
    "http://localhost:99999/v1", "ftp://localhost/v1",
])
def test_rejected_urls(settings, url):
    with pytest.raises(ValueError, match="HTTPS except for HTTP loopback local development"):
        replace(settings, base_url=url).validate()


@pytest.mark.parametrize("changes", [
    {"api_key": ""}, {"model": ""}, {"api_key": "bad\nkey"},
    {"timeout_seconds": 0}, {"max_output_tokens": 0}, {"temperature": 3},
    {"budget_usd": -1}, {"input_price_per_million": -1, "output_price_per_million": 1},
    {"input_price_per_million": 1}, {"output_price_per_million": float("nan")},
])
def test_other_validation_preserved(settings, changes):
    with pytest.raises(ValueError):
        replace(settings, base_url="http://localhost:11434/v1", **changes).validate()
