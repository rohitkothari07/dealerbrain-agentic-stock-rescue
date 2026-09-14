"""Repository contracts tested against isolated synthetic DuckDB tables."""

import ast
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path

import duckdb
import pytest

from models import Claim, Dealer, InventoryRecord, KnowledgeRecord, Part, PurchaseOrder, Shipment
from repositories import Repository, RepositoryError


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / "test.duckdb"
    with duckdb.connect(str(path)) as db:
        db.execute("""CREATE TABLE dealers (
            dealer_id VARCHAR, dealer_name VARCHAR, region VARCHAR, country VARCHAR,
            city VARCHAR, tier VARCHAR, status VARCHAR, contact_email VARCHAR,
            credit_limit_eur BIGINT, onboarded_date VARCHAR)""")
        db.execute("""CREATE TABLE parts (
            part_no VARCHAR, part_name VARCHAR, category VARCHAR, unit_price_eur DOUBLE,
            currency VARCHAR, warranty_months BIGINT, supplier_id VARCHAR, supplier_name VARCHAR,
            hazmat_flag VARCHAR, status VARCHAR, lead_time_days BIGINT)""")
        db.execute("""CREATE TABLE purchase_orders (
            po_no VARCHAR, po_line_no BIGINT, dealer_id VARCHAR, part_no VARCHAR,
            order_qty BIGINT, unit_price_eur DOUBLE, line_total_eur DOUBLE, order_date VARCHAR,
            requested_delivery_date VARCHAR, po_status VARCHAR, shipment_id VARCHAR, currency VARCHAR)""")
        db.execute("""CREATE TABLE inventory (
            inventory_id VARCHAR, part_no VARCHAR, warehouse_loc VARCHAR, on_hand_qty BIGINT,
            reserved_qty BIGINT, available_qty BIGINT, reorder_point BIGINT,
            bin_location VARCHAR, last_count_date VARCHAR)""")
        db.execute("""CREATE TABLE shipments (
            shipment_id VARCHAR, po_no VARCHAR, dealer_id VARCHAR, carrier VARCHAR,
            ship_date VARCHAR, est_delivery_date VARCHAR, actual_delivery_date VARCHAR,
            shipment_status VARCHAR, tracking_no VARCHAR, qty BIGINT)""")
        db.execute("""CREATE TABLE claims (
            claim_id VARCHAR, claim_type VARCHAR, dealer_id VARCHAR, part_no VARCHAR,
            po_no VARCHAR, claim_qty BIGINT, purchase_date VARCHAR, claim_date VARCHAR,
            claim_status VARCHAR, reason VARCHAR, claim_amount_eur DOUBLE, resolution_code VARCHAR)""")
        db.execute("""CREATE TABLE knowledge (
            doc_id VARCHAR, doc_type VARCHAR, title VARCHAR, module VARCHAR,
            error_code VARCHAR, summary VARCHAR, last_updated VARCHAR, owner_team VARCHAR)""")
        db.execute(
            "INSERT INTO dealers (dealer_id, dealer_name, status) VALUES ('001', 'Test Dealer', 'Suspended'), ('002', 'Other Dealer', 'Active')"
        )
        db.execute(
            "INSERT INTO dealers (dealer_id, dealer_name) VALUES (?, ?)",
            ["D'quoted", "Quoted Dealer"],
        )
        db.execute(
            "INSERT INTO parts (part_no, part_name, unit_price_eur) VALUES ('P-test', 'Test Part', 12.5)"
        )
        db.execute(
            "INSERT INTO purchase_orders (po_no, po_line_no, dealer_id, part_no, order_qty) VALUES ('PO-test', 2, '001', 'P-test', 3), ('PO-test', 1, '001', 'P-test', 2), ('PO-other', 1, '002', 'P-test', 1)"
        )
        db.execute(
            "INSERT INTO inventory (inventory_id, part_no, warehouse_loc, available_qty) VALUES ('I-b', 'P-test', 'W-b', -1), ('I-a', 'P-test', 'W-a', 5), ('I-c', 'P-other', 'W-a', 2)"
        )
        db.execute(
            "INSERT INTO shipments (shipment_id, po_no, dealer_id) VALUES ('S-test', 'PO-test', '001')"
        )
        db.execute(
            "INSERT INTO claims (claim_id, dealer_id, part_no) VALUES ('C-b', '001', 'P-test'), ('C-a', '001', 'P-test'), ('C-other', '002', 'P-test')"
        )
        db.execute(
            "INSERT INTO knowledge (doc_id, title, summary) VALUES ('K-test', 'Test title', 'Synthetic technical text')"
        )
        # Synthetic canaries only; no content from the supplied documentation sheets.
        db.execute("CREATE TABLE ANSWER_KEY (excluded_marker VARCHAR)")
        db.execute("INSERT INTO ANSWER_KEY VALUES ('synthetic exclusion canary')")
        db.execute("CREATE TABLE README (excluded_marker VARCHAR)")
    return path


@pytest.fixture
def repo(database_path):
    return Repository(database_path)


def test_dealer_and_nulls(repo):
    result = repo.get_dealer("001")
    assert isinstance(result.data, Dealer)
    assert result.data.dealer_id == "001"
    assert isinstance(result.data.dealer_id, str)
    assert result.data.dealer_name == "Test Dealer"
    assert result.data.contact_email is None
    assert result.data.status == "Suspended"  # Retrieval makes no eligibility decision.
    assert result.evidence[0].source_table == "dealers"
    assert result.evidence[0].record_key == "001"
    with pytest.raises(FrozenInstanceError):
        result.data.dealer_name = "Changed"
    with pytest.raises(FrozenInstanceError):
        result.evidence[0].record_key = "Changed"


def test_part(repo):
    result = repo.get_part("P-test")
    assert isinstance(result.data, Part)
    assert result.data.part_no == "P-test"
    assert result.data.unit_price_eur == 12.5
    assert result.evidence[0].source_table == "parts"


def test_purchase_order_all_lines_and_evidence(repo):
    result = repo.get_purchase_order("PO-test")
    assert all(isinstance(row, PurchaseOrder) for row in result.data)
    assert [row.po_line_no for row in result.data] == [1, 2]
    assert all(row.po_no == "PO-test" for row in result.data)
    assert [json.loads(e.record_key) for e in result.evidence] == [["PO-test", 1], ["PO-test", 2]]
    assert all(e.source_table == "purchase_orders" for e in result.evidence)


@pytest.mark.parametrize(
    "method", ["get_dealer", "get_part", "get_claim", "get_shipment", "get_knowledge_record"]
)
def test_missing_single_record(repo, method):
    result = getattr(repo, method)("missing")
    assert result.data is None
    assert result.evidence == ()


@pytest.mark.parametrize(
    "method",
    [
        "get_purchase_order",
        "get_inventory_for_part",
        "get_inventory_for_location",
        "list_claims_for_dealer",
        "list_purchase_orders_for_dealer",
    ],
)
def test_missing_list_records(repo, method):
    result = getattr(repo, method)("missing")
    assert result.data == []
    assert result.evidence == ()


def test_inventory_uses_real_location_and_preserves_anomaly(repo):
    result = repo.get_inventory_for_part("P-test")
    assert all(isinstance(row, InventoryRecord) for row in result.data)
    assert [row.inventory_id for row in result.data] == ["I-a", "I-b"]
    assert [row.available_qty for row in result.data] == [5, -1]
    assert [e.record_key for e in result.evidence] == ["I-a", "I-b"]
    assert all(e.source_table == "inventory" for e in result.evidence)
    assert [r.inventory_id for r in repo.get_inventory_for_location("W-a").data] == ["I-a", "I-c"]


def test_dealer_lists(repo):
    assert [r.po_no for r in repo.list_purchase_orders_for_dealer("001").data] == [
        "PO-test",
        "PO-test",
    ]
    assert [r.claim_id for r in repo.list_claims_for_dealer("001").data] == ["C-a", "C-b"]


@pytest.mark.parametrize(
    "method,key,model,table",
    [
        ("get_claim", "C-a", Claim, "claims"),
        ("get_shipment", "S-test", Shipment, "shipments"),
        ("get_knowledge_record", "K-test", KnowledgeRecord, "knowledge"),
    ],
)
def test_remaining_models(repo, method, key, model, table):
    result = getattr(repo, method)(key)
    assert isinstance(result.data, model)
    assert result.evidence[0].record_key == key
    assert result.evidence[0].source_table == table


def test_bound_parameters_and_no_writes(repo, database_path):
    before = hashlib.sha256(database_path.read_bytes()).hexdigest()
    assert repo.get_dealer("D'quoted").data.dealer_name == "Quoted Dealer"
    for key in ["' OR 1=1 --", "'; DROP TABLE dealers; --", "ANSWER_KEY", "", "001 "]:
        assert repo.get_dealer(key).data is None
    with pytest.raises(TypeError, match="strings"):
        repo.get_dealer(1)
    assert repo.get_dealer("001").data.dealer_id == "001"
    assert hashlib.sha256(database_path.read_bytes()).hexdigest() == before


def test_answer_key_unavailable_through_every_operation(repo):
    methods = {name for name in dir(Repository) if not name.startswith("_")}
    assert methods == {
        "get_dealer",
        "get_part",
        "get_purchase_order",
        "get_shipment",
        "get_claim",
        "get_inventory_for_part",
        "get_inventory_for_location",
        "get_knowledge_record",
        "list_claims_for_dealer",
        "list_purchase_orders_for_dealer",
        "list_inventory",
        "list_purchase_orders",
        "list_claims",
    }
    for name in methods:
        if name in {"list_inventory", "list_purchase_orders", "list_claims"}:
            result = getattr(repo, name)()
            assert all(e.source_table not in {"ANSWER_KEY", "README"} for e in result.evidence)
            assert "synthetic exclusion canary" not in repr(result)
            continue
        for value in ["ANSWER_KEY", "README", "' UNION SELECT * FROM ANSWER_KEY --"]:
            result = getattr(repo, name)(value)
            assert result.data is None or result.data == []
            assert not result.evidence
    with pytest.raises(ValueError, match="Unsupported"):
        repo._lookup("SELECT * FROM ANSWER_KEY", "")


def test_missing_database(tmp_path):
    path = tmp_path / "absent.duckdb"
    with pytest.raises(RepositoryError, match="Run python data_loader.py"):
        Repository(path).get_dealer("001")
    assert not path.exists()


def test_invalid_database_schema(tmp_path):
    path = tmp_path / "empty.duckdb"
    duckdb.connect(str(path)).close()
    with pytest.raises(RepositoryError, match="schema"):
        Repository(path).get_part("P-test")


def test_duplicate_single_key_is_not_silently_selected(database_path):
    with duckdb.connect(str(database_path)) as db:
        db.execute("INSERT INTO dealers (dealer_id) VALUES ('001')")
    with pytest.raises(RepositoryError, match="ambiguous"):
        Repository(database_path).get_dealer("001")


def test_models_have_only_standard_library_imports():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "models.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    assert imported <= {"dataclasses", "typing"}


def test_logs_are_concise(repo, caplog):
    with caplog.at_level("INFO", logger="repositories"):
        repo.get_dealer("001")
        repo.get_dealer("missing")
    assert "operation=get_dealer" in caplog.text
    assert "status=not-found" in caplog.text
    assert "Test Dealer" not in caplog.text
