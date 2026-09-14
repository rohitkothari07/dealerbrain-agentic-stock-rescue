"""Application branding and project-relative paths."""

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
