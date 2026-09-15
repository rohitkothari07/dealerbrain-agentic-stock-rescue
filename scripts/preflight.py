"""Local deployment checks only; no ingestion, provider probes or business writes."""

from contextlib import redirect_stdout, redirect_stderr
import importlib
import io
from pathlib import Path
import sys
from tempfile import TemporaryFile

ROOT = Path(__file__).resolve().parents[1]


def check_preflight(root=ROOT):
    failures = []
    if sys.version_info < (3, 11):
        failures.append("Python 3.11+ is required.")
    if not (root / "data" / "after_sales.xlsx").is_file():
        failures.append("Source workbook data/after_sales.xlsx is missing.")
    try:
        runtime = root / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        with TemporaryFile(dir=runtime) as probe:
            probe.write(b"preflight")
            probe.flush()
    except OSError:
        failures.append("runtime/ cannot be created or written.")
    for module in ("streamlit", "duckdb", "pandas", "dotenv", "openpyxl", "sqlite3", "app"):
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                importlib.import_module(module)
        except Exception:
            failures.append(f"Required module {module} could not be imported.")
    try:
        config = importlib.import_module("config")
        config.LLMSettings.from_environment()
    except Exception:
        failures.append("Application/LLM configuration is invalid; check local environment settings.")
    return tuple(failures)


def main():
    sys.path.insert(0, str(ROOT))
    failures = check_preflight()
    for failure in failures:
        print("FAIL: " + failure)
    if not failures:
        print("PASS: local preflight checks; provider connectivity was not tested.")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
