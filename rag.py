"""Deterministic lexical retrieval from operational Knowledge only; no generated answers."""

from collections import Counter
from dataclasses import dataclass
import re

from models import EvidenceRef, KnowledgeRecord, Part
from repositories import Repository, RepositoryError

_STOPWORDS = frozenset("a an the and or of to in on for is are be how what my can do does with".split())


def _tokens(text):
    return set(re.findall(r"[^\W_]+", text.lower())) - _STOPWORDS if isinstance(text, str) else set()


@dataclass(frozen=True)
class KnowledgeMatch:
    record: KnowledgeRecord
    score: int
    evidence: EvidenceRef


@dataclass(frozen=True)
class KnowledgeRetrieval:
    query: str
    matches: tuple[KnowledgeMatch, ...]
    status: str


def retrieve_knowledge(query: str, limit: int = 3, *, repository=None) -> KnowledgeRetrieval:
    """Score distinct overlaps: title 3, type/module/error code 2, summary 1."""
    if not isinstance(query, str) or type(limit) is not int or not 1 <= limit <= 100:
        return KnowledgeRetrieval(query if isinstance(query, str) else "", (), "INVALID_INPUT")
    tokens = _tokens(query)
    if not tokens:
        return KnowledgeRetrieval(query, (), "NO_MATCH")
    repository = repository if repository is not None else Repository()
    try:
        source = repository.list_knowledge()
    except RepositoryError:
        return KnowledgeRetrieval(query, (), "UNAVAILABLE")
    if len(source.data) != len(source.evidence):
        return KnowledgeRetrieval(query, (), "UNAVAILABLE")
    counts = Counter(row.doc_id for row in source.data)
    matches = []
    for row, evidence in zip(source.data, source.evidence):
        if (not isinstance(row.doc_id, str) or not row.doc_id.strip() or counts[row.doc_id] != 1
                or evidence.source_table != "knowledge" or evidence.record_key != row.doc_id):
            continue  # Never return ambiguous or non-operational provenance.
        score = sum(weight * len(tokens & _tokens(text)) for text, weight in (
            (row.title, 3), (row.doc_type, 2), (row.module, 2), (row.error_code, 2), (row.summary, 1),
        ))
        if score:
            matches.append(KnowledgeMatch(row, score, evidence))
    matches.sort(key=lambda match: (-match.score, match.evidence.record_key))
    return KnowledgeRetrieval(query, tuple(matches[:limit]), "MATCHED" if matches else "NO_MATCH")


@dataclass(frozen=True)
class PartMatch:
    record: Part
    score: int
    evidence: EvidenceRef


@dataclass(frozen=True)
class PartRetrieval:
    query: str
    matches: tuple[PartMatch, ...]
    status: str


def retrieve_parts(query: str, limit: int = 5, *, repository=None) -> PartRetrieval:
    """Score distinct overlaps against the parts catalog: name 3, category 2, supplier 1.

    Used to turn a free-text description (e.g. a vision-agent photo description) into
    ranked catalog candidates. The description is never treated as an authoritative part
    match on its own; only rows actually present in the parts table can be returned.
    """
    if not isinstance(query, str) or type(limit) is not int or not 1 <= limit <= 100:
        return PartRetrieval(query if isinstance(query, str) else "", (), "INVALID_INPUT")
    tokens = _tokens(query)
    if not tokens:
        return PartRetrieval(query, (), "NO_MATCH")
    repository = repository if repository is not None else Repository()
    try:
        source = repository.list_parts()
    except RepositoryError:
        return PartRetrieval(query, (), "UNAVAILABLE")
    if len(source.data) != len(source.evidence):
        return PartRetrieval(query, (), "UNAVAILABLE")
    counts = Counter(row.part_no for row in source.data)
    matches = []
    for row, evidence in zip(source.data, source.evidence):
        if (not isinstance(row.part_no, str) or not row.part_no.strip() or counts[row.part_no] != 1
                or evidence.source_table != "parts" or evidence.record_key != row.part_no):
            continue  # Never return ambiguous or non-operational provenance.
        score = sum(weight * len(tokens & _tokens(text)) for text, weight in (
            (row.part_name, 3), (row.category, 2), (row.supplier_name, 1),
        ))
        if score:
            matches.append(PartMatch(row, score, evidence))
    matches.sort(key=lambda match: (-match.score, match.evidence.record_key))
    return PartRetrieval(query, tuple(matches[:limit]), "MATCHED" if matches else "NO_MATCH")
