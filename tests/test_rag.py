"""Lexical retrieval checks with a Knowledge-only fake and operational smoke queries."""

import socket
from unittest.mock import Mock

import pytest

from models import EvidenceRef, KnowledgeRecord, Part, RepositoryResult
from rag import retrieve_knowledge, retrieve_parts
from repositories import Repository


def row(key, title, summary=""):
    return KnowledgeRecord(key, None, title, None, None, summary, None, None)


def source(*records):
    return Mock(spec=["list_knowledge"], list_knowledge=Mock(return_value=RepositoryResult(
        list(records), tuple(EvidenceRef("knowledge", r.doc_id) for r in records),
    )))


def part_row(part_no, part_name, category=None, supplier_name=None):
    return Part(part_no, part_name, category, None, None, None, None, supplier_name, None, None, None)


def part_source(*records):
    return Mock(spec=["list_parts"], list_parts=Mock(return_value=RepositoryResult(
        list(records), tuple(EvidenceRef("parts", r.part_no) for r in records),
    )))


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("No network allowed")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


def test_ranking_limit_and_evidence():
    records = [row("K2", "Warranty", "policy"), row("K1", "Warranty", "policy"),
               row("K3", "Other", "warranty"), row("K4", "Unrelated")]
    result = retrieve_knowledge("WARRANTY?!", 2, repository=source(*records))
    assert result.status == "MATCHED"
    assert [m.record.doc_id for m in result.matches] == ["K1", "K2"]
    assert [m.score for m in result.matches] == [3, 3]
    assert result.matches[0].record is records[1]
    assert result.matches[0].evidence == EvidenceRef("knowledge", "K1")
    assert retrieve_knowledge("WARRANTY?!", 2, repository=source(*reversed(records))) == result
    assert retrieve_knowledge("warranty", 2, repository=source(*records)).matches == result.matches


@pytest.mark.parametrize("query", ["", "?!", "the and of", "astrophysics"])
def test_no_match(query):
    assert retrieve_knowledge(query, repository=source(row("K", "Warranty"))).status == "NO_MATCH"


@pytest.mark.parametrize("limit", [0, -1, True, 101])
def test_invalid_limit(limit):
    repo = source()
    assert retrieve_knowledge("warranty", limit, repository=repo).status == "INVALID_INPUT"
    repo.list_knowledge.assert_not_called()


def test_excluded_provenance():
    repo = source(row("K", "Warranty"))
    repo.list_knowledge.return_value = RepositoryResult(
        [row("K", "Warranty")], (EvidenceRef("ANSWER_KEY", "K"),),
    )
    assert retrieve_knowledge("Warranty", repository=repo).matches == ()


@pytest.mark.parametrize("query, key, score", [
    ("suspended dealer", "KB-009", 7),
    ("negative available stock", "KB-007", 11),
    ("hazmat shipping", "KB-017", 7),
])
def test_operational_smoke(query, key, score):
    repo = Repository()
    result = retrieve_knowledge(query, repository=repo)
    match = result.matches[0]
    assert match.evidence == EvidenceRef("knowledge", key)
    assert match.record == repo.get_knowledge_record(key).data
    assert match.score == score


def test_parts_ranking_limit_and_evidence():
    records = [
        part_row("P2", "Brake Disc", "Brake"),
        part_row("P1", "Brake Disc", "Brake"),
        part_row("P3", "Brake Pad", "Suspension"),
        part_row("P4", "Cooling Hose", "Cooling"),
    ]
    result = retrieve_parts("brake disc", 2, repository=part_source(*records))
    assert result.status == "MATCHED"
    assert [m.record.part_no for m in result.matches] == ["P1", "P2"]
    assert [m.score for m in result.matches] == [8, 8]
    assert result.matches[0].record is records[1]
    assert result.matches[0].evidence == EvidenceRef("parts", "P1")


@pytest.mark.parametrize("query", ["", "?!", "the and of", "astrophysics"])
def test_parts_no_match(query):
    result = retrieve_parts(query, repository=part_source(part_row("P1", "Brake Disc")))
    assert result.status == "NO_MATCH"


@pytest.mark.parametrize("limit", [0, -1, True, 101])
def test_parts_invalid_limit(limit):
    repo = part_source()
    assert retrieve_parts("brake", limit, repository=repo).status == "INVALID_INPUT"
    repo.list_parts.assert_not_called()


def test_parts_excluded_provenance():
    repo = part_source(part_row("P1", "Brake Disc"))
    repo.list_parts.return_value = RepositoryResult(
        [part_row("P1", "Brake Disc")], (EvidenceRef("ANSWER_KEY", "P1"),),
    )
    assert retrieve_parts("brake disc", repository=repo).matches == ()


def test_parts_operational_smoke():
    repo = Repository()
    result = retrieve_parts("Brake Disc 256", repository=repo)
    match = result.matches[0]
    assert match.evidence == EvidenceRef("parts", "P-10003")
    assert match.record == repo.get_part("P-10003").data
    assert match.score == 11
