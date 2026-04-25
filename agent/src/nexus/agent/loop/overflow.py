"""Pre-flight context-overflow detection for chat turns.

When a session's history grows past a model's context window, providers like
z.ai silently return HTTP 200 with empty content — indistinguishable, at the
agent layer, from a model that genuinely had nothing to say. This module
estimates the request size up-front so the agent can refuse the turn with an
actionable error instead of looping on `empty_response`.

Token counting is intentionally cheap: chars/4 with a small overhead per
message. Real tokenization belongs to the upstream — we only need to be
roughly right (within ~30%) to flag obvious overflows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable


# Conservative chars-per-token. Mixed Portuguese/English/JSON averages closer
# to 3.5; we round down to 4 to under-count and only flag clear overflows.
_CHARS_PER_TOKEN = 4
# Per-message overhead (role markers, separators, etc).
_PER_MESSAGE_TOKENS = 4
# Headroom kept for the model's own output and any system prompt the
# provider injects. If the request alone fills the window, the model has
# zero budget left for a reply.
_OUTPUT_HEADROOM_TOKENS = 2048


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


def estimate_tokens(messages: Iterable[Any]) -> int:
    """Rough chars/4 estimate, plus a flat per-message overhead."""
    total = 0
    n = 0
    for m in messages:
        text = _msg_text(m)
        total += len(text)
        n += 1
        # Tool-call payloads carried alongside content
        tcs = getattr(m, "tool_calls", None) or (m.get("tool_calls") if isinstance(m, dict) else None)
        if tcs:
            try:
                total += len(json.dumps(tcs, default=str, ensure_ascii=False))
            except (TypeError, ValueError):
                total += sum(len(str(tc)) for tc in tcs)
    return total // _CHARS_PER_TOKEN + n * _PER_MESSAGE_TOKENS


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
