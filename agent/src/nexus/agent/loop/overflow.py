"""Nexus-specific context-overflow helpers.

The token estimator (``estimate_tokens``) and chars/token selector
(``_chars_per_token``) are re-exported from :mod:`loom.overflow` — that's the
single canonical implementation now. This module keeps only what's nexus-
specific:

* ``KNOWN_WINDOWS`` + ``known_context_window`` — the per-model context-window
  registry (loom is model-agnostic).
* ``check_overflow`` — like loom's, but applies a 32K fallback window when the
  model's window is unknown, so pre-flight detection still runs for
  unconfigured models.
* Budget constants (``_OUTPUT_HEADROOM_TOKENS``, ``_TOOLS_AND_SYSTEM_OVERHEAD``,
  ``_DEFAULT_FALLBACK_WINDOW``) and ``check_message_count``.

See :mod:`loom.overflow` for the estimator rationale (chars/token heuristic,
dense ratio for non-ASCII / JSON).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

# The token estimator (chars/token heuristic + per-message overhead) and the
# chars-per-token selector are owned by loom.overflow now — the canonical
# implementation. This module re-exports them under the historical nexus names
# so the many existing call sites are unchanged, and keeps only the
# nexus-specific concerns: KNOWN_WINDOWS, the fallback-window check_overflow,
# and budget constants. ``check_overflow`` deliberately differs from loom's
# (it applies a 32K fallback when the model window is unknown).
from loom.overflow import _chars_per_token  # noqa: F401 — re-exported
from loom.overflow import estimate_input_tokens as estimate_tokens  # noqa: F401

_OUTPUT_HEADROOM_TOKENS = 4096
_DEFAULT_FALLBACK_WINDOW = 32_000
_DEFAULT_MAX_MESSAGES = 80
# Approximate token cost of system prompt + all tool JSON Schema definitions
# sent with every request. With ~46 tools, the tool payloads alone consume
# ~10-12K tokens; the system prompt adds another ~2-3K.
_TOOLS_AND_SYSTEM_OVERHEAD = 12_000

KNOWN_WINDOWS: dict[str, int] = {
    "gemini-2.5-flash": 1_048_576,
    "gemini-2.5-pro": 1_048_576,
    "gemini-2.0-flash": 1_048_576,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4.1": 1_047_576,
    "gpt-4.1-mini": 1_047_576,
    "gpt-4.1-nano": 1_047_576,
    "o3": 200_000,
    "o4-mini": 200_000,
    "claude-sonnet-4-20250514": 200_000,
    "claude-3.5-sonnet": 200_000,
    "claude-3.7-sonnet": 200_000,
    "glm-4.7": 128_000,
    "glm-5": 200_000,
    "glm-5.1": 200_000,
    "deepseek-r1": 128_000,
    "deepseek-chat": 128_000,
    "nexus": 200_000,
}


def known_context_window(model: str) -> int:
    if not model:
        return 0
    name = model.split("/")[-1]
    exact = KNOWN_WINDOWS.get(name, 0)
    if exact:
        return exact
    for key, window in KNOWN_WINDOWS.items():
        if key in name:
            return window
    return 0


@dataclass
class OverflowCheck:
    overflowed: bool
    estimated_input_tokens: int
    context_window: int
    headroom: int
    detail: str | None = None


def check_overflow(
    messages: Iterable[Any],
    *,
    context_window: int,
    output_headroom: int = _OUTPUT_HEADROOM_TOKENS,
    tools_overhead: int = _TOOLS_AND_SYSTEM_OVERHEAD,
) -> OverflowCheck:
    est = estimate_tokens(messages)
    effective_window = context_window if context_window > 0 else _DEFAULT_FALLBACK_WINDOW
    budget = effective_window - output_headroom - tools_overhead
    if budget < 0:
        budget = 0
    if est <= budget:
        return OverflowCheck(False, est, context_window, output_headroom)
    pct = est * 100 // max(1, effective_window)
    window_label = f"{context_window:,}" if context_window > 0 else f"{_DEFAULT_FALLBACK_WINDOW:,} (fallback)"
    detail = (
        f"Conversation is too large for this model: ~{est:,} input tokens "
        f"vs. {window_label} window ({pct}% of capacity, no room for a "
        f"reply). Compact the history or start a new session."
    )
    return OverflowCheck(True, est, context_window, output_headroom, detail)


def check_message_count(
    messages: Iterable[Any],
    limit: int = _DEFAULT_MAX_MESSAGES,
) -> bool:
    n = 0
    for _ in messages:
        n += 1
        if n > limit:
            return True
    return False
