"""Lexical retrieval checks with a Knowledge-only fake and operational smoke queries."""

import socket
from unittest.mock import Mock

import pytest

from models import EvidenceRef, KnowledgeRecord, RepositoryResult
from rag import retrieve_knowledge
from repositories import Repository


def row(key, title, summary=""):
    return KnowledgeRecord(key, None, title, None, None, summary, None, None)


def source(*records):
    return Mock(spec=["list_knowledge"], list_knowledge=Mock(return_value=RepositoryResult(
        list(records), tuple(EvidenceRef("knowledge", r.doc_id) for r in records),
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
