"""Helper functions for the builtin entity extractor."""

from __future__ import annotations

import json
import re
from typing import Any

from ._constants import _NUMERIC_RE, _STOP_NOUNS

# ---------------------------------------------------------------------------
# Prompt parsing
# ---------------------------------------------------------------------------

_TYPES_RE = re.compile(r"Entity types to look for:\s*(.+?)(?:\n\n|\r\n\r\n)", re.DOTALL)
_TEXT_RE = re.compile(r"Text:\n(.+)", re.DOTALL)
_RESPOND_MARKER = "\n\nRespond with ONLY"


def _parse_prompt(prompt: str) -> tuple[list[str], str]:
    """Return ``(entity_types, text)`` from the extraction prompt."""
    entity_types: list[str] = []
    text = ""

    m = _TYPES_RE.search(prompt)
    if m:
        entity_types = [t.strip().lower() for t in m.group(1).split(",") if t.strip()]

    m = _TEXT_RE.search(prompt)
    if m:
        raw = m.group(1)
        idx = raw.find(_RESPOND_MARKER)
        text = (raw[:idx] if idx > 0 else raw).strip()

    return entity_types, text


def _has_capitalized_token(name: str) -> bool:
    """True if the name contains at least one capitalized non-stopword token."""
    for tok in name.split():
        if tok and tok[0].isupper() and tok.lower() not in _STOP_NOUNS:
            return True
    return False


def _is_quality_entity(name: str) -> bool:
    """Gate: reject noise entities before type classification."""
    # Too short
    if len(name) < 3:
        return False
    # Purely numeric / money
    if _NUMERIC_RE.match(name.replace(" ", "")):
        return False
    # Known stop word
    if name.lower() in _STOP_NOUNS:
        return False
    return True


def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb + 1e-8)


def _make_response(data: dict[str, Any]) -> Any:
    """Build a :class:`loom.types.ChatResponse` with JSON content."""
    from loom.types import ChatMessage, ChatResponse, Role, StopReason, Usage

    return ChatResponse(
        message=ChatMessage(role=Role.ASSISTANT, content=json.dumps(data)),
        usage=Usage(),
        stop_reason=StopReason.STOP,
        model="builtin-extractor",
    )
