"""Internal DuckDB lifecycle; future business callers use repositories.Repository."""

from contextlib import contextmanager
import json
from pathlib import Path

import duckdb

from config import DATABASE_PATH
from data_validation import OPERATIONAL_SHEETS, validate_tables

OPERATIONAL_TABLES = tuple(OPERATIONAL_SHEETS.values())


@contextmanager
def get_connection(path=DATABASE_PATH, read_only=True):
    """Open a short-lived connection; operational callers default to read-only."""
    connection = duckdb.connect(str(Path(path)), read_only=read_only)
    try:
        yield connection
    finally:
        connection.close()


def list_operational_tables(path=DATABASE_PATH):
    with get_connection(path) as connection:
        existing = {r[0] for r in connection.execute("SHOW TABLES").fetchall()}
    return [name for name in OPERATIONAL_TABLES if name in existing]


def get_table_metadata(path=DATABASE_PATH):
    with get_connection(path) as connection:
        return json.loads(connection.execute("SELECT payload FROM _dataset_metadata").fetchone()[0])


def read_table(table, path=DATABASE_PATH):
    """Internal ingestion verification helper; business callers use Repository."""
    if table not in OPERATIONAL_TABLES:
        raise ValueError(f"Not an operational table: {table}")
    with get_connection(path) as connection:
        return connection.execute(f'SELECT * FROM "{table}"').fetchdf()


def initialize_database(dataset, path=DATABASE_PATH):
    """Publish all validated tables and provenance in one transaction."""
    if set(dataset.tables) != set(OPERATIONAL_TABLES):
        raise ValueError("Only the eight required operational tables may be loaded.")
    if any(i.severity == "ERROR" for i in validate_tables(dataset.tables)):
        raise ValueError("Structural validation failed; database unchanged.")
    path = Path(path)
    if path.exists():
        with get_connection(path) as connection:
            existing = {r[0] for r in connection.execute("SHOW TABLES").fetchall()}
            if existing == set(OPERATIONAL_TABLES) | {"_dataset_metadata"}:
                previous = json.loads(connection.execute(
                    "SELECT payload FROM _dataset_metadata").fetchone()[0])
                if (previous["sha256"] == dataset.metadata["sha256"]
                        and previous["source_filename"] == dataset.metadata["source_filename"]
                        and previous["tables"] == dataset.metadata["tables"]
                        and previous["issues"] == dataset.metadata["issues"]):
                    return previous
            if existing - set(OPERATIONAL_TABLES) - {"_dataset_metadata"}:
                raise ValueError("Unexpected database tables; use a fresh runtime database.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(path, read_only=False) as connection:
        connection.execute("BEGIN TRANSACTION")
        try:
            for table in OPERATIONAL_TABLES:
                connection.register("_source_frame", dataset.tables[table])
                try:
                    connection.execute(f'CREATE OR REPLACE TABLE "{table}" AS SELECT * FROM _source_frame')
                finally:
                    connection.unregister("_source_frame")
            connection.execute("CREATE OR REPLACE TABLE _dataset_metadata (payload VARCHAR)")
            connection.execute("INSERT INTO _dataset_metadata VALUES (?)",
                               [json.dumps(dataset.metadata)])
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
    return dataset.metadata
