"""Vision agent: LLM output is interpretive only; catalog matches stay deterministic."""

from unittest.mock import Mock

import pytest

from llm_client import FakeLLMClient, LLMDisabledError, LLMResponse
from rag import PartMatch
from models import EvidenceRef, Part
import vision


def fake_client(text):
    return FakeLLMClient(text)


def match(part_no, name):
    return PartMatch(
        Part(part_no, name, "Brake", None, None, None, None, None, None, None, None),
        11,
        EvidenceRef("parts", part_no),
    )


@pytest.mark.parametrize(
    "image_bytes,mime_type",
    [
        pytest.param(b"", "image/png", id="empty-bytes"),
        pytest.param(b"data", "application/pdf", id="bad-mime"),
        pytest.param(b"x" * 5_000_001, "image/png", id="oversized-image"),
        pytest.param(None, "image/png", id="none-bytes"),
    ],
)
def test_invalid_input_never_calls_llm(image_bytes, mime_type):
    client = Mock()
    result = vision.identify_part_from_photo(image_bytes, mime_type, client)
    assert result.status == "INVALID_INPUT"
    assert result.description == ""
    assert result.candidates == ()
    client.describe_image.assert_not_called()


def test_llm_unavailable_is_surfaced_without_facts(monkeypatch):
    client = Mock()
    client.describe_image.side_effect = LLMDisabledError("disabled")
    result = vision.identify_part_from_photo(b"photo-bytes", "image/png", client)
    assert result.status == "UNAVAILABLE"
    assert result.description == ""
    assert result.candidates == ()


def test_blank_description_is_unavailable():
    client = Mock()
    client.describe_image.return_value = LLMResponse("   ", "mock")
    result = vision.identify_part_from_photo(b"photo-bytes", "image/png", client)
    assert result.status == "UNAVAILABLE"


def test_description_matched_against_deterministic_catalog(monkeypatch):
    from rag import PartRetrieval

    client = fake_client("A worn brake disc with visible scoring.")
    matches = (match("P-10003", "Brake Disc 256"),)
    monkeypatch.setattr(
        vision, "retrieve_parts",
        lambda query, limit: PartRetrieval(query, matches, "MATCHED"),
    )
    result = vision.identify_part_from_photo(b"photo-bytes", "image/png", client)
    assert result.status == "DESCRIBED"
    assert result.description == "A worn brake disc with visible scoring."
    assert result.candidates == matches


def test_no_catalog_match_reports_no_match(monkeypatch):
    from rag import PartRetrieval

    client = fake_client("An unidentifiable metal fragment.")
    monkeypatch.setattr(
        vision, "retrieve_parts", lambda query, limit: PartRetrieval(query, (), "NO_MATCH"),
    )
    result = vision.identify_part_from_photo(b"photo-bytes", "image/png", client)
    assert result.status == "NO_MATCH"
    assert result.candidates == ()


def test_repository_unavailable_propagates(monkeypatch):
    from rag import PartRetrieval

    client = fake_client("A worn brake disc.")
    monkeypatch.setattr(
        vision, "retrieve_parts", lambda query, limit: PartRetrieval(query, (), "UNAVAILABLE"),
    )
    result = vision.identify_part_from_photo(b"photo-bytes", "image/png", client)
    assert result.status == "UNAVAILABLE"
    assert result.description == "A worn brake disc."
