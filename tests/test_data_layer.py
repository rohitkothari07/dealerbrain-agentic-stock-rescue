"""Deterministic synthetic tests; no source answer-key content is used."""

from dataclasses import asdict
import hashlib

import duckdb
import pandas as pd
import pytest

from data_loader import (
    DataInitializationError, dataset_fingerprint, initialize_data, load_workbook,
    normalize_column_name, operational_sheet_names,
)
from data_validation import OPERATIONAL_SHEETS, REQUIRED_COLUMNS, validate_tables
from database import get_connection, get_table_metadata, list_operational_tables, read_table


@pytest.fixture
def tables():
    return {name: pd.DataFrame(columns=sorted(columns))
            for name, columns in REQUIRED_COLUMNS.items()}


def workbook(tmp_path, tables, extra=True):
    path = tmp_path / "fixture.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet, table in OPERATIONAL_SHEETS.items():
            if table in tables:
                tables[table].to_excel(writer, sheet_name=sheet, index=False)
        if extra:
            # Only a synthetic marker, unrelated to the supplied answer key.
            pd.DataFrame({"excluded_marker": ["not operational"]}).to_excel(
                writer, sheet_name="ANSWER_KEY", index=False)
            pd.DataFrame({"documentation": ["not operational"]}).to_excel(
                writer, sheet_name="README", index=False)
    return path


@pytest.mark.parametrize("source,expected", [
    ("DealerID", "dealer_id"), ("PartNo", "part_no"), ("PO_No", "po_no"),
    ("ShipmentID", "shipment_id"), ("AvailableQty", "available_qty"),
    ("PO_LineNo", "po_line_no"), ("BOMID", "bom_id"),
    (" CreditLimitEUR ", "credit_limit_eur"), ("a--b", "a_b"),
])
def test_normalization(source, expected):
    assert normalize_column_name(source) == expected


def test_filtering():
    assert operational_sheet_names(["README", "ANSWER_KEY", "Parts", "Dealers", "Other"]) == [
        "Dealers", "Parts"]


def test_missing_workbook(tmp_path):
    path = tmp_path / "absent.xlsx"
    with pytest.raises(DataInitializationError, match="Place the supplied Excel workbook") as error:
        load_workbook(path)
    assert str(path) in str(error.value)
    assert not path.exists()


def test_unreadable_workbook(tmp_path):
    path = tmp_path / "invalid.xlsx"
    path.write_bytes(b"not an Excel file")
    with pytest.raises(DataInitializationError, match="valid, unencrypted"):
        load_workbook(path)


def test_missing_sheet(tmp_path, tables):
    del tables["claims"]
    issues = validate_tables(tables)
    assert any(i.code == "MISSING_SHEET" and i.severity == "ERROR" for i in issues)
    with pytest.raises(DataInitializationError, match="Claims"):
        load_workbook(workbook(tmp_path, tables))


def test_missing_column(tables):
    tables["parts"] = pd.DataFrame({"wrong_column": []})
    assert any(i.code == "MISSING_COLUMN" and i.table == "parts" for i in validate_tables(tables))


def test_duplicate_key_and_no_mutation(tables):
    tables["dealers"] = pd.DataFrame({"dealer_id": ["D-test", "D-test"]})
    original = tables["dealers"].copy(deep=True)
    issues = validate_tables(tables)
    assert sum(i.code == "DUPLICATE_KEY" for i in issues) == 2
    assert all(i.severity != "ERROR" for i in issues)
    pd.testing.assert_frame_equal(original, tables["dealers"])


def test_composite_purchase_order_key(tables):
    tables["purchase_orders"] = pd.DataFrame({
        "po_no": ["PO-test", "PO-test"], "po_line_no": [1, 2],
        "dealer_id": ["D-test", "D-test"], "part_no": ["P-test", "P-test"],
    })
    assert not any(i.code == "DUPLICATE_KEY" for i in validate_tables(tables))


def test_unknown_references(tables):
    tables["claims"] = pd.DataFrame({
        "claim_id": ["C-test"], "dealer_id": ["D-unknown"], "part_no": ["P-unknown"],
        "po_no": ["PO-unknown"], "shipment_id": ["S-unknown"],
    })
    issues = [i for i in validate_tables(tables) if i.code == "UNKNOWN_REFERENCE"]
    assert len(issues) == 4
    assert all(i.severity == "WARNING" for i in issues)


def test_negative_inventory_and_warehouse(tables):
    tables["inventory"] = pd.DataFrame({
        "inventory_id": ["I-test"], "warehouse_loc": ["W-test"],
        "part_no": ["P-test"], "available_qty": [-2],
    })
    issues = validate_tables(tables)
    assert any(i.code == "NEGATIVE_QUANTITY" and i.table == "inventory" for i in issues)
    assert any(i.code == "WAREHOUSE_INVENTORY" and i.severity == "INFO" for i in issues)
    assert tables["inventory"].iloc[0]["available_qty"] == -2


def test_missing_business_value_is_warning(tables):
    tables["knowledge"] = pd.DataFrame({"doc_id": ["T-test"], "title": [""], "summary": ["Text"]})
    assert any(i.code == "MISSING_VALUE" and i.severity == "WARNING"
               for i in validate_tables(tables))


def test_currency_and_inventory_consistency(tables):
    tables["parts"] = pd.DataFrame({"part_no": ["P-test"], "currency": ["USD"]})
    tables["inventory"] = pd.DataFrame({
        "inventory_id": ["I-test"], "warehouse_loc": ["W-test"], "part_no": ["P-test"],
        "on_hand_qty": [10], "reserved_qty": [2], "available_qty": [9],
    })
    codes = {i.code for i in validate_tables(tables)}
    assert {"UNEXPECTED_CURRENCY", "INVENTORY_MISMATCH"} <= codes


def test_fingerprint(tmp_path):
    path = tmp_path / "source.xlsx"
    path.write_bytes(b"fixed source bytes")
    assert dataset_fingerprint(path) == hashlib.sha256(path.read_bytes()).hexdigest()
    assert dataset_fingerprint(path) == dataset_fingerprint(path)
    before = dataset_fingerprint(path)
    path.write_bytes(b"changed source bytes")
    assert dataset_fingerprint(path) != before


def test_ingestion_read_only_idempotence_and_exclusion(tmp_path, tables):
    tables["dealers"] = pd.DataFrame({"dealer_id": ["001", "D-test", "NA"]})
    tables["inventory"] = pd.DataFrame({
        "inventory_id": ["I-test"], "warehouse_loc": ["W-test"],
        "part_no": ["missing-part"], "available_qty": [-2],
    })
    source = workbook(tmp_path, tables)
    before = source.read_bytes()
    path = tmp_path / "runtime" / "test.duckdb"
    metadata = initialize_data(source, path)
    assert set(list_operational_tables(path)) == set(OPERATIONAL_SHEETS.values())
    assert read_table("dealers", path)["dealer_id"].tolist() == ["001", "D-test", "NA"]
    assert read_table("inventory", path)["available_qty"].tolist() == [-2]
    assert metadata["issues"] == [asdict(i) for i in validate_tables(load_workbook(source).tables)]
    with pytest.raises(ValueError, match="Not an operational table"):
        read_table("ANSWER_KEY", path)
    with get_connection(path) as connection:
        assert {r[0] for r in connection.execute("SHOW TABLES").fetchall()} == (
            set(OPERATIONAL_SHEETS.values()) | {"_dataset_metadata"})
        with pytest.raises(duckdb.InvalidInputException):
            connection.execute("DELETE FROM dealers")
    database_before = path.read_bytes()
    assert initialize_data(source, path) == metadata
    assert path.read_bytes() == database_before
    assert source.read_bytes() == before
    assert get_table_metadata(path) == metadata
    # Failed initialization must not replace an existing valid database.
    tables["parts"] = pd.DataFrame({"invalid": []})
    workbook(tmp_path, tables)
    with pytest.raises(DataInitializationError, match="Missing columns"):
        initialize_data(source, path)
    assert path.read_bytes() == database_before


def test_normalized_header_collision(tmp_path, tables):
    tables["dealers"] = pd.DataFrame(columns=["DealerID", "dealer_id"])
    with pytest.raises(DataInitializationError, match="duplicate normalized"):
        load_workbook(workbook(tmp_path, tables))
