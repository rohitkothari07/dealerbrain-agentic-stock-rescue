"""Optional OpenAI-compatible HTTP adapter; no network until explicit chat invocation."""

from dataclasses import dataclass
import json
from http.client import HTTPException
import logging
import math
import socket
from threading import RLock
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from config import LLMSettings
from prompts import DEALERBRAIN_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Safe adapter error; provider payloads and credentials are never included."""


class LLMDisabledError(LLMError):
    pass


class LLMConfigurationError(LLMError):
    pass


class LLMAuthenticationError(LLMError):
    pass


class LLMRateLimitError(LLMError):
    pass


class LLMTimeoutError(LLMError):
    pass


class LLMProviderError(LLMError):
    pass


class LLMBudgetError(LLMError):
    pass


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: float | None = None
    latency_ms: float = 0.0
    request_id: str | None = None


@dataclass(frozen=True)
class UsageSnapshot:
    request_count: int
    successful_requests: int
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    estimated_spend_usd: float | None


class UsageTracker:
    """Process-local attempts and usage; missing usage is not silently counted as zero."""

    def __init__(self):
        self.lock = RLock()
        self.request_count = 0
        self.successful_requests = 0
        self.uncertain_attempts = 0
        self._totals = dict.fromkeys(
            ("prompt_tokens", "completion_tokens", "total_tokens", "estimated_cost_usd"), 0
        )

    def record(self, response):
        with self.lock:
            self.successful_requests += 1
            for name, previous in self._totals.items():
                value = getattr(response, name)
                self._totals[name] = None if previous is None or value is None else previous + value

    def snapshot(self):
        with self.lock:

            def total(name):
                if self.uncertain_attempts or not self.successful_requests:
                    return None
                return self._totals[name]

            return UsageSnapshot(
                self.request_count,
                self.successful_requests,
                total("prompt_tokens"),
                total("completion_tokens"),
                total("total_tokens"),
                total("estimated_cost_usd"),
            )

    def check_budget(self, settings):
        if settings.budget_usd is None or settings.input_price_per_million is None:
            return  # Unknown pricing cannot enforce a dollar ceiling.
        spent = self.snapshot().estimated_spend_usd
        if self.request_count and spent is None:
            raise LLMBudgetError(
                "Budget accounting is incomplete; further priced calls are blocked."
            )
        if (spent or 0) >= settings.budget_usd:
            raise LLMBudgetError("Configured soft budget has been reached.")


PROCESS_USAGE = UsageTracker()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _post_json(settings, payload):
    """One bounded HTTPS attempt, no redirects/proxies/retries, no SDK dependency."""
    try:
        request = Request(
            settings.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": "Bearer " + settings.api_key,
                "Content-Type": "application/json",
            },
        )
        with build_opener(ProxyHandler({}), _NoRedirect()).open(
            request, timeout=settings.timeout_seconds
        ) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise LLMProviderError("Provider response exceeded the size limit.")
            return json.loads(raw), response.headers.get("x-request-id")
    except HTTPError as exc:
        status = exc.code
        exc.close()
        error = (
            LLMAuthenticationError
            if status in {401, 403}
            else LLMRateLimitError
            if status == 429
            else LLMProviderError
        )
        raise error(f"LLM request failed (HTTP {status}).") from None
    except (TimeoutError, socket.timeout):
        raise LLMTimeoutError("LLM request timed out.") from None
    except URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise LLMTimeoutError("LLM request timed out.") from None
        raise LLMProviderError("LLM endpoint could not be reached.") from None
    except (OSError, ValueError, HTTPException, RecursionError):
        raise LLMProviderError("LLM provider returned an unreadable response.") from None


def _token(usage, name):
    value = usage.get(name)
    return value if type(value) is int and value >= 0 else None


def _parse_response(data, settings, latency, header_id=None):
    try:
        message = data["choices"][0]["message"]
        text = message["content"]
        if not isinstance(text, str) or message.get("tool_calls") or message.get("function_call"):
            raise ValueError
        usage = data.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        prompt, completion, total = (
            _token(usage, name) for name in ("prompt_tokens", "completion_tokens", "total_tokens")
        )
        cost = None
        if (
            settings.input_price_per_million is not None
            and prompt is not None
            and completion is not None
        ):
            cost = (
                prompt * settings.input_price_per_million
                + completion * settings.output_price_per_million
            ) / 1_000_000
            if not math.isfinite(cost):
                raise ValueError
        model = data.get("model", settings.model)
        request_id = header_id or data.get("id")
        if not isinstance(model, str) or (
            request_id is not None and not isinstance(request_id, str)
        ):
            raise ValueError
        return LLMResponse(text, model, prompt, completion, total, cost, latency, request_id)
    except (KeyError, IndexError, TypeError, ValueError, OverflowError):
        raise LLMProviderError(
            "LLM provider response did not match the supported text schema."
        ) from None


class LLMClient:
    """Configured text-chat boundary. Never receives database connections or tool executors."""

    def __init__(self, settings=None, *, tracker=None):
        self._settings = settings
        self.usage = tracker if tracker is not None else PROCESS_USAGE

    def chat(self, messages, *, temperature=None, max_tokens=None):
        try:
            settings = (
                self._settings if self._settings is not None else LLMSettings.from_environment()
            )
            settings.validate()
        except (TypeError, ValueError):
            raise LLMConfigurationError("LLM configuration is incomplete or invalid.") from None
        if not settings.enabled:
            raise LLMDisabledError("LLM adapter is disabled.")
        temperature = settings.temperature if temperature is None else temperature
        max_tokens = settings.max_output_tokens if max_tokens is None else max_tokens
        if type(max_tokens) is not int or not 0 < max_tokens <= settings.max_output_tokens:
            raise LLMConfigurationError("Requested output tokens exceed the configured limit.")
        if type(temperature) not in (int, float) or not 0 <= temperature <= 2:
            raise LLMConfigurationError("Temperature must be between zero and two.")
        if not isinstance(messages, (list, tuple)) or not messages:
            raise LLMConfigurationError("Provide a non-empty list of text messages.")
        safe_messages = []
        for message in messages:
            if (
                not isinstance(message, dict)
                or set(message) != {"role", "content"}
                or not isinstance(message["role"], str)
                or message["role"] not in {"system", "user", "assistant"}
                or not isinstance(message["content"], str)
            ):
                raise LLMConfigurationError("Only role/content text messages are supported.")
            safe_messages.append(dict(message))
        if sum(len(m["content"]) for m in safe_messages) > 32_000:
            raise LLMConfigurationError("Input exceeds the 32000-character adapter limit.")
        payload = {
            "model": settings.model,
            "messages": [{"role": "system", "content": DEALERBRAIN_SYSTEM_PROMPT}, *safe_messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # Serialize priced calls on this tracker so concurrent calls cannot bypass the soft ceiling.
        with self.usage.lock:
            self.usage.check_budget(settings)
            self.usage.request_count += 1
            started = time.perf_counter()
            try:
                data, request_id = _post_json(settings, payload)
                result = _parse_response(
                    data, settings, (time.perf_counter() - started) * 1000, request_id
                )
            except LLMError:
                self.usage.uncertain_attempts += 1
                logger.warning("LLM request failed; provider details omitted.")
                raise
            self.usage.record(result)
            logger.info(
                "LLM request completed; usage_available=%s", result.total_tokens is not None
            )
            return result


class FakeLLMClient:
    """Explicit deterministic local substitute, never an automatic production fallback."""

    def __init__(
        self,
        text="Deterministic mock response.",
        *,
        model="mock",
        prompt_tokens=None,
        completion_tokens=None,
        total_tokens=None,
    ):
        self._response = LLMResponse(text, model, prompt_tokens, completion_tokens, total_tokens)
        self.usage = UsageTracker()

    def chat(self, messages, *, temperature=None, max_tokens=None):
        with self.usage.lock:
            self.usage.request_count += 1
            self.usage.record(self._response)
        return self._response


@dataclass(frozen=True)
class LLMStatus:
    state: str
    detail: str


def get_llm_status():
    """Non-sensitive local configuration health; never probes or displays provider details."""
    try:
        settings = LLMSettings.from_environment()
    except (ValueError, TypeError):
        return LLMStatus("Not Configured", "LLM configuration is incomplete or invalid.")
    if not settings.enabled:
        return LLMStatus("Disabled", "Deterministic tools remain available.")
    return LLMStatus(
        "Configured", "Configuration validated locally; this status does not test connectivity."
    )
