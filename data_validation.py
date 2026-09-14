"""Non-mutating structural and business-data validation for the source schema."""

from dataclasses import dataclass

import pandas as pd

OPERATIONAL_SHEETS = {
    "Dealers": "dealers", "Parts": "parts", "PurchaseOrders": "purchase_orders",
    "Shipments": "shipments", "Claims": "claims", "Inventory": "inventory",
    "BOM": "bom", "Knowledge": "knowledge",
}
KEYS = {
    "dealers": ("dealer_id",), "parts": ("part_no",),
    "purchase_orders": ("po_no", "po_line_no"), "shipments": ("shipment_id",),
    "claims": ("claim_id",), "inventory": ("inventory_id",),
    "bom": ("bom_id",), "knowledge": ("doc_id",),
}
REQUIRED_COLUMNS = {
    "dealers": {"dealer_id"}, "parts": {"part_no"},
    "purchase_orders": {"po_no", "po_line_no", "dealer_id", "part_no"},
    "shipments": {"shipment_id", "po_no", "dealer_id"},
    "claims": {"claim_id", "dealer_id", "part_no", "po_no"},
    "inventory": {"inventory_id", "warehouse_loc", "part_no", "available_qty"},
    "bom": {"bom_id", "assembly_part_no", "component_part_no", "qty_per"},
    "knowledge": {"doc_id", "title", "summary"},
}


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    table: str
    record_key: str | None
    message: str


def blank(series):
    return series.isna() | series.map(lambda v: isinstance(v, str) and not v.strip())


def validate_tables(tables):
    """Return errors for unusable schema and warnings for preserved anomalies."""
    issues = []
    for table, required in REQUIRED_COLUMNS.items():
        if table not in tables:
            issues.append(ValidationIssue("ERROR", "MISSING_SHEET", table, None,
                                          f"Required sheet {table} is missing."))
        else:
            missing = required - set(tables[table].columns)
            if missing:
                issues.append(ValidationIssue("ERROR", "MISSING_COLUMN", table, None,
                                              f"Missing columns: {', '.join(sorted(missing))}."))
    if issues:
        return issues

    def report(table, mask, code, message):
        frame = tables[table]
        for position in range(len(frame)):
            if bool(mask.iloc[position]):
                row = frame.iloc[position]
                key = "|".join(str(row[c]) for c in KEYS[table])
                issues.append(ValidationIssue("WARNING", code, table, key, message))

    for table, frame in tables.items():
        keys = list(KEYS[table])
        report(table, frame[keys].apply(blank).any(axis=1), "MISSING_KEY",
               "Business key contains an empty value.")
        report(table, frame.duplicated(keys, keep=False), "DUPLICATE_KEY",
               f"Duplicate business key ({', '.join(keys)}).")
        for column in sorted(REQUIRED_COLUMNS[table] - set(keys)):
            report(table, blank(frame[column]), "MISSING_VALUE",
                   f"Required business field {column} is empty.")
        for column in frame.columns:
            if column.endswith("_qty") or column in {"qty", "qty_per", "reorder_point"}:
                values = pd.to_numeric(frame[column], errors="coerce")
                report(table, values.isna() & ~blank(frame[column]), "INVALID_QUANTITY",
                       f"{column} is not numeric.")
                report(table, values < 0, "NEGATIVE_QUANTITY", f"{column} is negative.")
            if column == "currency":
                # EUR is the currency named by this workbook's monetary columns.
                report(table, ~frame[column].eq("EUR"), "UNEXPECTED_CURRENCY",
                       "Currency differs from EUR-denominated source price columns.")

    relationships = [
        ("purchase_orders", "dealer_id", "dealers", "dealer_id"),
        ("purchase_orders", "part_no", "parts", "part_no"),
        ("purchase_orders", "shipment_id", "shipments", "shipment_id"),
        ("claims", "part_no", "parts", "part_no"),
        ("claims", "dealer_id", "dealers", "dealer_id"),
        ("claims", "shipment_id", "shipments", "shipment_id"),
        ("claims", "po_no", "purchase_orders", "po_no"),
        ("shipments", "po_no", "purchase_orders", "po_no"),
        ("shipments", "dealer_id", "dealers", "dealer_id"),
        ("inventory", "part_no", "parts", "part_no"),
        ("inventory", "dealer_id", "dealers", "dealer_id"),
        ("bom", "assembly_part_no", "parts", "part_no"),
        ("bom", "component_part_no", "parts", "part_no"),
    ]
    for table, column, parent, parent_key in relationships:
        if column in tables[table]:
            values = tables[table][column]
            report(table, ~blank(values) & ~values.isin(tables[parent][parent_key]),
                   "UNKNOWN_REFERENCE", f"{column} has no match in {parent}.{parent_key}.")
    inventory = tables["inventory"]
    if "dealer_id" not in inventory:
        issues.append(ValidationIssue("INFO", "WAREHOUSE_INVENTORY", "inventory", None,
                                      "Inventory uses warehouse_loc; no dealer relationship assumed."))
    if {"on_hand_qty", "reserved_qty", "available_qty"} <= set(inventory.columns):
        numbers = inventory[["on_hand_qty", "reserved_qty", "available_qty"]].apply(
            pd.to_numeric, errors="coerce")
        mismatch = numbers["available_qty"] != numbers["on_hand_qty"] - numbers["reserved_qty"]
        report("inventory", mismatch & numbers.notna().all(axis=1), "INVENTORY_MISMATCH",
               "Available quantity differs from on-hand minus reserved quantity.")
    return issues
