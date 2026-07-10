"""Relevance scoring for context retention.

The historical compaction strategy was purely positional: split history at a
fixed offset, summarize the head, keep the tail. That drops a critical old
message (a key file read, a stated constraint) just because it's old, while
preserving a recent but irrelevant tool result.

This module scores each message by relevance to the *current* turn so the
retention partitioner (``loop/retention.py``) can keep high-signal messages
verbatim regardless of age, and only collapse the genuinely low-relevance
tail into the session-memory summary.

Scoring is intentionally cheap and deterministic — regex entity extraction,
exponential recency decay, role weighting, and an explicit ``nx:pin`` marker.
No embeddings here (those are a Phase 4 opt-in behind the ``knowledge``
feature). The signals are additive and documented so the partitioner can
apply thresholds that are robust to tuning.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from nexus.agent.llm import ChatMessage, Role

# ── weights ────────────────────────────────────────────────────────────────
# Additive factors. A most-recent USER message with several entity overlaps
# lands ~0.8; an old, no-overlap TOOL result lands ~0.05. The partitioner
# treats anything below the relevant threshold as summarizable.

_W_RECENCY = 0.35      # weight on the exponential recency term
_W_ENTITY = 0.30       # max contribution from query entity overlap
_W_SEMANTIC = 0.35     # max contribution from embedding cosine similarity
_W_PIN = 0.50          # explicit ``nx:pin`` marker contribution
_RECENCY_LAMBDA = 0.06  # decay constant; ~0.06 keeps recent msgs dominant

_ROLE_WEIGHTS: dict[Role, float] = {
    Role.USER: 0.18,       # user intent is sticky
    Role.ASSISTANT: 0.12,  # decisions / reasoning
    Role.SYSTEM: 0.06,
    Role.TOOL: 0.03,       # tool results are the most compressible
}

# Full entity-overlap credit at this many overlapping entities.
_ENTITY_SATURATION = 3.0

_PIN_MARKER = "nx:pin"

# ── entity extraction ──────────────────────────────────────────────────────
# High-signal, low-noise tokens: backtick code spans, slashy paths, URLs, and
# ``nx:`` markers. Bare identifiers are skipped — too noisy without a parser.
_BACKTICK_RE = re.compile(r"`([^`\n]{1,120})`")
_PATH_RE = re.compile(r"(?:^|[\s(])([A-Za-z0-9_.\-/]+/[A-Za-z0-9_.\-/]+)")
_URL_RE = re.compile(r"https?://[^\s)\"\]]{4,120}")
_NX_RE = re.compile(r"(nx:[a-z_]+=[^\s`)]{1,80})")


def _content_text(msg: ChatMessage) -> str:
    """Flatten a ChatMessage's content to a plain string for analysis.

    Multipart bodies (image/audio/document uploads) contribute only their
    text parts — the vault paths of binaries aren't useful relevance signal.
    """
    content = msg.content
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    # list[ContentPart]
    parts = []
    for p in content:
        if getattr(p, "kind", None) == "text" and p.text:
            parts.append(p.text)
    return " ".join(parts)


def extract_entities(text: str) -> frozenset[str]:
    """Pull high-signal tokens out of a message/query string.

    Returns lowercased tokens so cross-message matching is case-insensitive.
    Empty / whitespace-only spans are dropped.
    """
    if not text:
        return frozenset()
    tokens: set[str] = set()
    for m in _BACKTICK_RE.finditer(text):
        tok = m.group(1).strip().lower()
        if tok:
            tokens.add(tok)
    for m in _PATH_RE.finditer(text):
        tok = m.group(1).strip().lower()
        if tok:
            tokens.add(tok)
    for m in _URL_RE.finditer(text):
        tokens.add(m.group(0).lower())
    for m in _NX_RE.finditer(text):
        tokens.add(m.group(1).lower())
    return frozenset(tokens)


# ── scoring ────────────────────────────────────────────────────────────────


@dataclass
class MessageScore:
    """Per-message relevance score with a factor breakdown.

    ``factors`` is exposed so observability (logs, debug tools) can explain
    *why* a message was kept or summarized, not just the aggregate number.
    """

    index: int
    score: float
    factors: dict[str, float] = field(default_factory=dict)


def score_messages(
    messages: list[ChatMessage],
    *,
    query: str | None = None,
    semantic_sim: dict[int, float] | None = None,
) -> list[MessageScore]:
    """Score every message for relevance to the current turn.

    Args:
        messages: The full ordered history (oldest first).
        query: The current turn's focus — typically the latest user message
            text. Entity overlap with this is the dominant "what still
            matters" signal. ``None`` skips the entity factor.
        semantic_sim: Optional precomputed embedding cosine similarity per
            message index (``{index: 0..1}``). When supplied (Phase 4,
            ``knowledge`` feature), adds a semantic factor that catches
            meaning-level relevance the regex entity match misses. ``None``
            keeps scoring cheap, sync, and dependency-free.

    The returned list is index-aligned with ``messages`` (result[i] describes
    messages[i]).
    """
    n = len(messages)
    if n == 0:
        return []

    query_entities = extract_entities(query) if query else frozenset()

    out: list[MessageScore] = []
    for i, msg in enumerate(messages):
        text = _content_text(msg)
        distance = (n - 1) - i  # 0 at the tail, grows toward the head
        recency = math.exp(-_RECENCY_LAMBDA * distance)

        role_w = _ROLE_WEIGHTS.get(msg.role, 0.03)

        entity_component = 0.0
        if query_entities:
            msg_entities = extract_entities(text)
            overlap = len(query_entities & msg_entities)
            entity_component = (
                min(1.0, overlap / _ENTITY_SATURATION) * _W_ENTITY
            )

        semantic_component = 0.0
        if semantic_sim is not None:
            sim = semantic_sim.get(i, 0.0)
            semantic_component = max(0.0, min(1.0, sim)) * _W_SEMANTIC

        pin = _W_PIN if _PIN_MARKER in text else 0.0

        if pin:
            # An explicit ``nx:pin`` marker is a hard "keep always" directive:
            # force max score regardless of age or overlap so the partitioner
            # always routes it to the protected bucket.
            total = 1.0
        else:
            total = (
                recency * _W_RECENCY
                + role_w
                + entity_component
                + semantic_component
            )
            total = min(1.0, total)

        out.append(
            MessageScore(
                index=i,
                score=total,
                factors={
                    "recency": round(recency * _W_RECENCY, 4),
                    "role": role_w,
                    "entity": round(entity_component, 4),
                    "semantic": round(semantic_component, 4),
                    "pin": pin,
                },
            )
        )
    return out
