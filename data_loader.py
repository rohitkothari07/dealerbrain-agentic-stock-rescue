"""Deterministic, read-only Excel discovery and validated DuckDB ingestion."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import logging
from pathlib import Path
import re

import pandas as pd

from config import DATABASE_PATH, WORKBOOK_PATH
from data_validation import OPERATIONAL_SHEETS, ValidationIssue, validate_tables

logger = logging.getLogger(__name__)


class DataInitializationError(Exception):
    """Actionable structural failure that prevents data initialization."""

    def __init__(self, message, issues=()):
        super().__init__(message)
        self.issues = list(issues)


def normalize_column_name(name):
    name = re.sub(r"(?<=[A-Z])ID$", "_ID", str(name).strip())
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", str(name).strip())
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()


def operational_sheet_names(names):
    return [name for name in OPERATIONAL_SHEETS if name in names]


def source_bytes(path=WORKBOOK_PATH):
    path = Path(path)
    try:
        return path.read_bytes()
    except OSError as exc:
        raise DataInitializationError(
            f"Cannot read workbook at {path.resolve()}. Place the supplied Excel workbook "
            "there and ensure it is readable."
        ) from exc


def dataset_fingerprint(path=WORKBOOK_PATH):
    return hashlib.sha256(source_bytes(path)).hexdigest()


@dataclass
class Dataset:
    tables: dict
    metadata: dict
    issues: list


def load_workbook(path=WORKBOOK_PATH):
    """Read one byte snapshot; do not read documentation or answer-key cells."""
    content = source_bytes(path)
    tables = {}
    metadata = []
    try:
        with pd.ExcelFile(BytesIO(content), engine="openpyxl") as book:
            missing = set(OPERATIONAL_SHEETS) - set(book.sheet_names)
            if missing:
                issues = [ValidationIssue("ERROR", "MISSING_SHEET", OPERATIONAL_SHEETS[n],
                                          None, f"Required sheet {n} is missing.")
                          for n in sorted(missing)]
                raise DataInitializationError("Missing required sheets: " + ", ".join(sorted(missing)),
                                              issues)
            for sheet in operational_sheet_names(book.sheet_names):
                # Reading the header as data avoids pandas silently renaming duplicates.
                raw = pd.read_excel(book, sheet_name=sheet, header=None, dtype=object,
                                    keep_default_na=False)
                columns = [] if raw.empty else [normalize_column_name(c) for c in raw.iloc[0]]
                if not columns or any(not c for c in columns) or len(set(columns)) != len(columns):
                    issue = ValidationIssue("ERROR", "INVALID_COLUMNS", OPERATIONAL_SHEETS[sheet],
                                            None, "Empty or duplicate normalized column names.")
                    raise DataInitializationError(f"{sheet}: {issue.message}", [issue])
                frame = raw.iloc[1:].reset_index(drop=True).copy()
                frame.columns = columns
                # infer_objects never parses string identifiers or dates into numbers/dates.
                frame = frame.infer_objects()
                table = OPERATIONAL_SHEETS[sheet]
                tables[table] = frame
                entry = {"source_sheet": sheet, "table": table, "rows": len(frame),
                         "columns": columns, "dtypes": {c: str(t) for c, t in frame.dtypes.items()}}
                metadata.append(entry)
                logger.info("Discovered %s", entry)
    except DataInitializationError:
        raise
    except Exception as exc:
        raise DataInitializationError(
            f"Unable to read Excel workbook {Path(path).resolve()}. "
            "Check that it is a valid, unencrypted .xlsx file."
        ) from exc
    issues = validate_tables(tables)
    errors = [i for i in issues if i.severity == "ERROR"]
    if errors:
        raise DataInitializationError("; ".join(f"{i.table}: {i.message}" for i in errors), issues)
    return Dataset(tables, {
        "source_filename": Path(path).name, "sha256": hashlib.sha256(content).hexdigest(),
        "ingested_at": datetime.now(timezone.utc).isoformat(), "tables": metadata,
        "issues": [asdict(i) for i in issues],
    }, issues)


def initialize_data(workbook_path=WORKBOOK_PATH, database_path=DATABASE_PATH):
    from database import initialize_database

    dataset = load_workbook(workbook_path)
    try:
        return initialize_database(dataset, database_path)
    except Exception as exc:
        raise DataInitializationError(
            f"Cannot initialize DuckDB at {Path(database_path).resolve()}. "
            "Check directory permissions and close other database processes."
        ) from exc


if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        result = initialize_data()
    except DataInitializationError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps({k: v for k, v in result.items() if k != "issues"}, indent=2))
    print("Validation:", {s: sum(i["severity"] == s for i in result["issues"])
                          for s in ("ERROR", "WARNING", "INFO")})
