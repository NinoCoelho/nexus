"""Nexus-specific context-overflow helpers.

The chars/token selector (``_chars_per_token``) is re-exported from
:mod:`loom.overflow`. This module keeps what's nexus-specific:

* ``KNOWN_WINDOWS`` + ``known_context_window`` — the per-model context-window
  registry with family-prefix fallback matching (loom is model-agnostic).
* ``estimate_tokens`` — loom's estimator wrapped so **multipart content is
  counted**. A ``ContentPart`` carries a vault path, not bytes (the provider
  base64-encodes at request time), so the raw estimator sees a ~40-char path
  where the wire payload is ~1.6K tokens of image. Undercounting attachments
  was a silent-overflow source: the meter read green while the request
  blew the window.
* ``check_overflow`` — like loom's, but applies a fallback window when the
  model's window is unknown, so detection still runs for unconfigured models.
* The single source of truth for the budget constants
  (``OUTPUT_HEADROOM_TOKENS``, ``TOOLS_AND_SYSTEM_OVERHEAD``,
  ``DEFAULT_FALLBACK_WINDOW``) plus ``usable_tokens`` /
  ``check_message_count``.

**Do not re-declare these constants at a call site.** They used to be
duplicated across ``turn_launcher``, ``_builder``, ``compact`` and the
``sessions`` route with three different fallback windows and two different
headrooms, so the user-visible meter, the soft auto-compact trigger and the
hard overflow limit all described different windows. Import them from here.

See :mod:`loom.overflow` for the estimator rationale (chars/token heuristic,
dense ratio for non-ASCII / JSON).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

# ``_chars_per_token`` is both used here (multipart scoring) and re-exported
# for the historical call sites that import it from this module.
from loom.overflow import _chars_per_token
from loom.overflow import estimate_input_tokens as _loom_estimate_input_tokens

# ── budget constants (single source of truth) ──────────────────────────────
# Headroom reserved for the model's own reply. Matches the value handed to
# loom's AgentConfig (``overflow_output_headroom``) so nexus's soft
# auto-compact trigger and loom's hard overflow detection agree on how much
# of the window is actually usable.
OUTPUT_HEADROOM_TOKENS = 8192
# Conservative window for a model with no configured/known context window.
# Deliberately low: under-estimating the window makes us compact too eagerly
# (harmless), over-estimating it loses the user's turn (not harmless).
DEFAULT_FALLBACK_WINDOW = 32_000
# Hard cap on message count regardless of the token estimate — a long tail of
# small messages still costs per-message overhead and degrades recall.
DEFAULT_MAX_MESSAGES = 240
# Approximate token cost of system prompt + all tool JSON Schema definitions
# sent with every request. With ~46 tools, the tool payloads alone consume
# ~10-12K tokens; the system prompt adds another ~2-3K. MCP servers push this
# higher, which is why it's a floor, not a measurement.
TOOLS_AND_SYSTEM_OVERHEAD = 12_000

# Historical private aliases — kept so existing imports keep working.
_OUTPUT_HEADROOM_TOKENS = OUTPUT_HEADROOM_TOKENS
_DEFAULT_FALLBACK_WINDOW = DEFAULT_FALLBACK_WINDOW
_DEFAULT_MAX_MESSAGES = DEFAULT_MAX_MESSAGES
_TOOLS_AND_SYSTEM_OVERHEAD = TOOLS_AND_SYSTEM_OVERHEAD

# Measured replacement for the constant above, set once the tool registry and
# system prompt exist (see ``_builder.build_loom_agent``). The constant is a
# guess that under-counted reality by ~10K on a default install; budgeting
# against a guess means the remaining window is wrong in whichever direction
# the guess errs.
_measured_overhead: int | None = None


def set_measured_overhead(tokens: int) -> None:
    """Record the real system-prompt + tool-schema cost, in tokens."""
    global _measured_overhead
    if tokens and tokens > 0:
        _measured_overhead = int(tokens)


def invalidate_measured_overhead() -> None:
    """Forget the measurement — call when the tool set or skills change."""
    global _measured_overhead
    _measured_overhead = None


def tools_and_system_overhead() -> int:
    """Per-call overhead to budget against: measured if known, else the floor.

    Prefer this over reading ``TOOLS_AND_SYSTEM_OVERHEAD`` directly.
    """
    return _measured_overhead if _measured_overhead else TOOLS_AND_SYSTEM_OVERHEAD


# ── attachment accounting ──────────────────────────────────────────────────
# Per-attachment token surcharge, by ContentPart kind. These are deliberate
# over-estimates: a 1024px image lands ~1.1-1.6K tokens on Claude/GPT, and
# the cost of over-counting is an earlier compaction, while the cost of
# under-counting is a dead turn.
ATTACHMENT_TOKEN_COST: dict[str, int] = {
    "image": 1_600,
    "audio": 2_000,
    "document": 3_000,
}
_DEFAULT_ATTACHMENT_COST = 1_600


def _flatten_multipart(content: Any) -> tuple[Any, int]:
    """Return ``(text_only_content, attachment_token_surcharge)``.

    Non-list content passes through untouched with no surcharge. A
    ``list[ContentPart]`` collapses to its concatenated text parts, and each
    non-text part contributes its per-kind token cost.
    """
    if not isinstance(content, list):
        return content, 0
    texts: list[str] = []
    surcharge = 0
    for part in content:
        kind = getattr(part, "kind", None)
        if kind is None and isinstance(part, dict):
            kind = part.get("kind")
        if kind == "text":
            text = getattr(part, "text", None)
            if text is None and isinstance(part, dict):
                text = part.get("text")
            if text:
                texts.append(str(text))
            continue
        surcharge += ATTACHMENT_TOKEN_COST.get(
            str(kind), _DEFAULT_ATTACHMENT_COST
        )
    return ("\n".join(texts) if texts else ""), surcharge


def estimate_attachment_tokens(messages: Iterable[Any]) -> int:
    """Total attachment-only token surcharge across ``messages``."""
    total = 0
    for msg in messages:
        _, surcharge = _flatten_multipart(getattr(msg, "content", None))
        total += surcharge
    return total


# Loom's estimator adds a small fixed per-message overhead on top of the
# chars/token division; mirrored here for the messages we score ourselves so
# the two paths stay comparable.
_PER_MESSAGE_OVERHEAD = 4


def _estimate_multipart_message(content: list[Any]) -> int:
    """Token estimate for one multipart message, attachments included.

    Scored locally rather than by handing loom a substitute object: loom's
    estimator is free to read any attribute of the messages it is given, and
    a stand-in that merely looks message-shaped would be a latent
    AttributeError. Flattening here keeps the contract to "loom only ever
    sees the real messages".
    """
    flat, surcharge = _flatten_multipart(content)
    text = flat if isinstance(flat, str) else ""
    if text:
        surcharge += len(text) // _chars_per_token(text)
    return surcharge + _PER_MESSAGE_OVERHEAD


def estimate_tokens(messages: Iterable[Any]) -> int:
    """Estimated input tokens for ``messages``, attachments included.

    Single-part (string) bodies go to loom's canonical estimator untouched.
    Multipart bodies are scored here, because their real cost is the
    base64-encoded attachment the provider builds at request time, not the
    short vault path stored in history.
    """
    plain: list[Any] = []
    multipart_total = 0
    for msg in messages:
        content = getattr(msg, "content", None)
        if isinstance(content, list):
            multipart_total += _estimate_multipart_message(content)
        else:
            plain.append(msg)
    if not plain:
        return multipart_total
    return _loom_estimate_input_tokens(plain) + multipart_total


KNOWN_WINDOWS: dict[str, int] = {
    # Anthropic
    "claude-opus-4-1": 200_000,
    "claude-opus-4-5": 200_000,
    "claude-opus-4": 200_000,
    "claude-sonnet-4-5": 200_000,
    "claude-sonnet-4-20250514": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-haiku-4-5": 200_000,
    "claude-3.5-sonnet": 200_000,
    "claude-3.7-sonnet": 200_000,
    "claude-3-5-haiku": 200_000,
    # OpenAI
    "gpt-5.1": 400_000,
    "gpt-5-mini": 400_000,
    "gpt-5": 400_000,
    "gpt-4.1-mini": 1_047_576,
    "gpt-4.1-nano": 1_047_576,
    "gpt-4.1": 1_047_576,
    "gpt-4o-mini": 128_000,
    "gpt-4o": 128_000,
    "o4-mini": 200_000,
    "o3-mini": 200_000,
    "o3": 200_000,
    # Google
    "gemini-3-pro": 1_048_576,
    "gemini-3": 1_048_576,
    "gemini-2.5-flash": 1_048_576,
    "gemini-2.5-pro": 1_048_576,
    "gemini-2.0-flash": 1_048_576,
    # Others
    "glm-4.7": 128_000,
    "glm-5.1": 200_000,
    "glm-5": 200_000,
    "deepseek-r1": 128_000,
    "deepseek-chat": 128_000,
    "qwen3": 128_000,
    "kimi-k2": 128_000,
    "mistral-large": 128_000,
    "llama-4": 128_000,
    "nexus": 200_000,
}

# Family fallbacks, applied only when no KNOWN_WINDOWS entry matches. Keyed by
# a tuple of substrings that must ALL appear in the model name. Ordered
# most-specific first. This is what stops a model released after this file was
# written from silently collapsing to the 32K fallback — the registry can't
# know every id, but it can recognize a family.
_FAMILY_WINDOWS: tuple[tuple[tuple[str, ...], int], ...] = (
    (("claude", "opus"), 200_000),
    (("claude", "sonnet"), 200_000),
    (("claude", "haiku"), 200_000),
    (("claude",), 200_000),
    (("gemini",), 1_048_576),
    (("gpt-5",), 400_000),
    (("gpt-4.1",), 1_047_576),
    (("gpt-4",), 128_000),
    (("deepseek",), 128_000),
    (("qwen",), 128_000),
    (("glm",), 128_000),
    (("llama",), 128_000),
    (("mistral",), 128_000),
    (("mixtral",), 32_000),
)


def known_context_window(model: str) -> int:
    """Best-known context window for ``model``, or 0 when unrecognized.

    Resolution order: exact id, longest matching registry key, then family
    prefix. Callers should treat 0 as "apply ``DEFAULT_FALLBACK_WINDOW``"
    rather than "skip the check" — skipping is how an unknown model ended up
    with no protection at all.
    """
    if not model:
        return 0
    name = model.split("/")[-1].strip().lower()
    exact = KNOWN_WINDOWS.get(name, 0)
    if exact:
        return exact
    # Longest key first so "gpt-4.1-mini" doesn't match the shorter "gpt-4.1".
    for key in sorted(KNOWN_WINDOWS, key=len, reverse=True):
        if key in name:
            return KNOWN_WINDOWS[key]
    for needles, window in _FAMILY_WINDOWS:
        if all(n in name for n in needles):
            return window
    return 0


def effective_context_window(model: str, configured: int = 0) -> int:
    """Window to budget against: configured value, else known, else fallback.

    Never returns 0 — the caller always gets a usable number, which is what
    lets every gate run instead of silently disabling itself.
    """
    if configured and configured > 0:
        return int(configured)
    known = known_context_window(model)
    return known if known > 0 else DEFAULT_FALLBACK_WINDOW


def usable_tokens(
    context_window: int,
    *,
    output_headroom: int = OUTPUT_HEADROOM_TOKENS,
    tools_overhead: int | None = None,
) -> int:
    """Tokens available for conversation history, after reply + tool overhead.

    ``tools_overhead`` defaults to the measured value when one is available
    (see :func:`tools_and_system_overhead`).
    """
    window = context_window if context_window > 0 else DEFAULT_FALLBACK_WINDOW
    overhead = (
        tools_and_system_overhead() if tools_overhead is None else tools_overhead
    )
    return max(0, window - output_headroom - overhead)


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
    output_headroom: int = OUTPUT_HEADROOM_TOKENS,
    tools_overhead: int | None = None,
) -> OverflowCheck:
    est = estimate_tokens(messages)
    effective_window = context_window if context_window > 0 else DEFAULT_FALLBACK_WINDOW
    budget = usable_tokens(
        effective_window, output_headroom=output_headroom, tools_overhead=tools_overhead
    )
    if est <= budget:
        return OverflowCheck(False, est, context_window, output_headroom)
    pct = est * 100 // max(1, effective_window)
    window_label = (
        f"{context_window:,}" if context_window > 0 else f"{DEFAULT_FALLBACK_WINDOW:,} (fallback)"
    )
    detail = (
        f"Conversation is too large for this model: ~{est:,} input tokens "
        f"vs. {window_label} window ({pct}% of capacity, no room for a "
        f"reply). Compact the history or start a new session."
    )
    return OverflowCheck(True, est, context_window, output_headroom, detail)


def check_message_count(
    messages: Iterable[Any],
    limit: int = DEFAULT_MAX_MESSAGES,
) -> bool:
    """True when ``messages`` exceeds ``limit`` entries.

    Used as an extra compaction *trigger* (not an error): a history of many
    small messages can stay under the token budget while still carrying
    hundreds of stale turns that degrade recall.
    """
    if limit <= 0:
        return False
    n = 0
    for _ in messages:
        n += 1
        if n > limit:
            return True
    return False


def recent_k_for_window(context_window: int) -> int:
    """Verbatim working-set size to keep, scaled to the model's window.

    A fixed 20 messages is simultaneously too many for a 32K model and
    needlessly few for a 1M one. Roughly one message per 4K of usable window,
    clamped to a sane band.
    """
    usable = usable_tokens(context_window)
    return max(12, min(60, usable // 4_000))
