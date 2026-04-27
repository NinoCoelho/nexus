"""Pre-flight context-overflow detection for chat turns.

When a session's history grows past a model's context window, providers like
z.ai silently return HTTP 200 with empty content — indistinguishable, at the
agent layer, from a model that genuinely had nothing to say. This module
estimates the request size up-front so the agent can refuse the turn with an
actionable error instead of looping on `empty_response`.

Token counting is intentionally cheap. Real tokenization belongs to the
upstream — we only need to be roughly right (within ~30%) to flag obvious
overflows. Heuristics:

- Plain English text: ≈4 chars/token.
- Non-ASCII-heavy text (Portuguese, Chinese, Cyrillic, …) and JSON tool
  payloads (URL-rich, escape-rich): ≈3 chars/token. Tokenizers split runs of
  punctuation, accents, and percent-encoded URLs much more aggressively than
  English prose.

The previous chars/4 default systematically under-counted Portuguese + JSON
sessions and let real overflows slip past pre-flight.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable


# Per-message overhead (role markers, separators, etc).
_PER_MESSAGE_TOKENS = 4
# Headroom kept for the model's own output and the system prompt the agent
# loop injects (skills index, vault context, USER.md). Was 2048; bumped to
# 4096 because Nexus's system prompt with a populated skills index easily
# exceeds 2K tokens on its own, and z.ai gives no error when the combined
# request overflows — it just returns empty content.
_OUTPUT_HEADROOM_TOKENS = 4096

# Tunable chars/token ratios.
_CHARS_PER_TOKEN_ASCII = 4
_CHARS_PER_TOKEN_DENSE = 3
# Sample size for the per-message ratio decision. 512 chars is enough to
# detect non-ASCII or JSON without paying O(n) per long tool result.
_SAMPLE_LEN = 512
# Above this fraction of non-ASCII chars in the sample, treat the text as
# "dense" and use the lower chars/token ratio.
_NON_ASCII_THRESHOLD = 0.05


@dataclass
class OverflowCheck:
    overflowed: bool
    estimated_input_tokens: int
    context_window: int
    headroom: int
    detail: str | None = None


def _msg_text(msg: Any) -> str:
    """Best-effort extraction of the textual payload of a ChatMessage-like
    object. Handles Nexus ChatMessage (content str), tool messages (content
    is a JSON blob), and anything pydantic with a `.content` attr."""
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    # Loom-style content lists or dicts: stringify so we still account for
    # their size. Cheap and covers the common case.
    try:
        return json.dumps(content, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content)


def _chars_per_token(text: str) -> int:
    """Pick chars/token ratio for a text segment.

    JSON-shaped (starts with `[` / `{`) and non-ASCII-heavy text gets the
    denser ratio because tokenizers emit ~30% more tokens per char for those
    inputs. Plain English keeps the looser ratio.
    """
    if not text:
        return _CHARS_PER_TOKEN_ASCII
    sample = text[:_SAMPLE_LEN]
    stripped = sample.lstrip()
    if stripped[:1] in ("[", "{"):
        return _CHARS_PER_TOKEN_DENSE
    non_ascii = sum(1 for c in sample if ord(c) > 127)
    if non_ascii / max(1, len(sample)) > _NON_ASCII_THRESHOLD:
        return _CHARS_PER_TOKEN_DENSE
    return _CHARS_PER_TOKEN_ASCII


def estimate_tokens(messages: Iterable[Any]) -> int:
    """Rough chars/token estimate with per-message ratio + flat overhead.

    Tool-call payloads (always JSON) get the dense ratio unconditionally.
    """
    total = 0
    n = 0
    for m in messages:
        text = _msg_text(m)
        if text:
            total += len(text) // _chars_per_token(text)
        n += 1
        tcs = getattr(m, "tool_calls", None) or (
            m.get("tool_calls") if isinstance(m, dict) else None
        )
        if tcs:
            try:
                tc_text = json.dumps(tcs, default=str, ensure_ascii=False)
            except (TypeError, ValueError):
                tc_text = " ".join(str(tc) for tc in tcs)
            total += len(tc_text) // _CHARS_PER_TOKEN_DENSE
    return total + n * _PER_MESSAGE_TOKENS


def check_overflow(
    messages: Iterable[Any],
    *,
    context_window: int,
    output_headroom: int = _OUTPUT_HEADROOM_TOKENS,
) -> OverflowCheck:
    """Return an OverflowCheck describing whether this turn likely fits.

    `context_window=0` disables the check (caller hasn't configured a limit
    for the model).
    """
    est = estimate_tokens(messages)
    if context_window <= 0:
        return OverflowCheck(False, est, 0, 0)
    budget = context_window - output_headroom
    if est <= budget:
        return OverflowCheck(False, est, context_window, output_headroom)
    pct = est * 100 // max(1, context_window)
    detail = (
        f"Conversation is too large for this model: ~{est:,} input tokens "
        f"vs. {context_window:,} window ({pct}% of capacity, no room for a "
        f"reply). Compact the history or start a new session."
    )
    return OverflowCheck(True, est, context_window, output_headroom, detail)
