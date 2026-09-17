"""Photo-to-part vision agent.

The LLM only describes what is visible in a submitted photo; it never asserts a specific
catalog part number, price, stock, or claim outcome (see PROJECT_RULES.md). Candidate parts
are decided by deterministic lexical matching (rag.retrieve_parts) against the operational
parts catalog, exactly like Knowledge retrieval. A human still selects/confirms the actual
part before any stock or fulfillment check runs.
"""

from dataclasses import dataclass

from llm_client import LLMClient, LLMError
from rag import PartMatch, retrieve_parts

ALLOWED_IMAGE_TYPES = frozenset({"image/png", "image/jpeg"})
MAX_IMAGE_BYTES = 5_000_000

_PROMPT = (
    "This photo was submitted with an after-sales claim or stock request. "
    "Describe the part shown so it can be matched against a parts catalog."
)


@dataclass(frozen=True)
class VisionIdentification:
    status: str  # "DESCRIBED" | "NO_MATCH" | "UNAVAILABLE" | "INVALID_INPUT"
    description: str
    candidates: tuple[PartMatch, ...]


def identify_part_from_photo(
    image_bytes, mime_type, llm_client=None, *, limit=5
) -> VisionIdentification:
    """One vision call plus one deterministic catalog match; never a business decision."""
    if (
        not isinstance(image_bytes, (bytes, bytearray))
        or not image_bytes
        or mime_type not in ALLOWED_IMAGE_TYPES
        or len(image_bytes) > MAX_IMAGE_BYTES
    ):
        return VisionIdentification("INVALID_INPUT", "", ())
    client = llm_client if llm_client is not None else LLMClient()
    try:
        response = client.describe_image(image_bytes, mime_type, _PROMPT)
    except LLMError:
        return VisionIdentification("UNAVAILABLE", "", ())
    description = response.text.strip() if isinstance(response.text, str) else ""
    if not description:
        return VisionIdentification("UNAVAILABLE", "", ())
    retrieval = retrieve_parts(description, limit)
    if retrieval.status == "UNAVAILABLE":
        return VisionIdentification("UNAVAILABLE", description, ())
    return VisionIdentification(
        "DESCRIBED" if retrieval.matches else "NO_MATCH", description, retrieval.matches,
    )
