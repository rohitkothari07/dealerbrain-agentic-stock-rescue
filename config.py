"""Application branding and project-relative paths."""

from dataclasses import dataclass, field
import math
import os
from urllib.parse import urlsplit
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
WORKBOOK_PATH = DATA_DIR / "after_sales.xlsx"
RUNTIME_DIR = PROJECT_ROOT / "runtime"
DATABASE_PATH = RUNTIME_DIR / "dealerbrain.duckdb"
ENV_FILE = PROJECT_ROOT / ".env"

load_dotenv(dotenv_path=ENV_FILE, override=False)

APP_NAME = "DealerBRAIN"
APP_TAGLINE = "Agentic After-Sales Stock Rescue Copilot"
TEAM_NAME = "Stock Overflow"


# Optional adapter settings are parsed on demand, never during module import.


@dataclass(frozen=True)
class LLMSettings:
    enabled: bool = False
    api_key: str = field(default="", repr=False)
    base_url: str = field(default="", repr=False)
    model: str = field(default="", repr=False)
    timeout_seconds: float = 20.0
    max_output_tokens: int = 512
    temperature: float = 0.0
    budget_usd: float | None = None
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None

    @classmethod
    def from_environment(cls):
        enabled = os.getenv("LLM_ENABLED", "false").strip().lower()
        if enabled not in {"true", "false", "1", "0"}:
            raise ValueError("LLM_ENABLED must be true or false.")
        if enabled in {"false", "0"}:
            return cls()
        try:

            def optional(name):
                return float(os.environ[name]) if os.getenv(name) else None

            settings = cls(
                enabled=True,
                api_key=os.getenv("LLM_API_KEY", ""),
                base_url=os.getenv("LLM_BASE_URL", ""),
                model=os.getenv("LLM_MODEL", ""),
                timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "20")),
                max_output_tokens=int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "512")),
                temperature=float(os.getenv("LLM_TEMPERATURE", "0")),
                budget_usd=optional("LLM_BUDGET_USD"),
                input_price_per_million=optional("LLM_INPUT_PRICE_PER_MILLION"),
                output_price_per_million=optional("LLM_OUTPUT_PRICE_PER_MILLION"),
            )
        except (ValueError, TypeError):
            raise ValueError("Invalid numeric LLM configuration.") from None
        settings.validate()
        return settings

    def validate(self):
        if not self.enabled:
            return
        if not self.api_key.strip():
            raise ValueError("LLM_API_KEY is required when enabled.")
        if not self.model.strip():
            raise ValueError("LLM_MODEL is required when enabled.")
        try:
            url = urlsplit(self.base_url)
            valid_url = (
                url.scheme == "https"
                and bool(url.hostname)
                and not url.username
                and not url.password
                and not url.query
                and not url.fragment
            )
            _ = url.port
        except ValueError:
            valid_url = False
        if not valid_url:
            raise ValueError(
                "LLM_BASE_URL must be an HTTPS API base URL without embedded credentials."
            )
        if any(c in self.api_key for c in "\r\n"):
            raise ValueError("Invalid API credential format.")
        if not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 120:
            raise ValueError("LLM timeout must be greater than zero and at most 120 seconds.")
        if type(self.max_output_tokens) is not int or not 0 < self.max_output_tokens <= 32768:
            raise ValueError("LLM output limit must be an integer from 1 to 32768.")
        if not math.isfinite(self.temperature) or not 0 <= self.temperature <= 2:
            raise ValueError("LLM temperature must be between zero and two.")
        for value in (self.budget_usd, self.input_price_per_million, self.output_price_per_million):
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError("LLM budget and prices must be finite non-negative values.")
        if (self.input_price_per_million is None) != (self.output_price_per_million is None):
            raise ValueError("Configure both input and output prices, or neither.")
