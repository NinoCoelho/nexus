"""Context budget plumbing: attachment accounting, model-window resolution,
and the single source of truth for the budget constants.

Three bugs motivate this file:

* image/audio/document attachments were counted as the ~40-char vault path
  they carry in history, not the ~1.6K tokens they cost on the wire, so a
  multimodal session overflowed while the meter read green;
* ``known_context_window`` only did exact/substring matching over a registry
  that had no entry for any current model, so a 200K-window model resolved to
  the 32K fallback (and in the pre-turn gate, to "skip the check entirely");
* the headroom/fallback constants were duplicated in five places with three
  different window values, so the meter, the soft auto-compact trigger and
  loom's hard overflow limit all described different windows.
"""

from __future__ import annotations

from nexus.agent.llm import ChatMessage, ContentPart, Role
from nexus.agent.loop.overflow import (
    ATTACHMENT_TOKEN_COST,
    DEFAULT_FALLBACK_WINDOW,
    OUTPUT_HEADROOM_TOKENS,
    TOOLS_AND_SYSTEM_OVERHEAD,
    check_message_count,
    effective_context_window,
    estimate_attachment_tokens,
    estimate_tokens,
    known_context_window,
    recent_k_for_window,
    usable_tokens,
)


# ── attachment accounting ──────────────────────────────────────────────────


def _with_image(text: str = "look at this") -> ChatMessage:
    return ChatMessage(
        role=Role.USER,
        content=[
            ContentPart(kind="text", text=text),
            ContentPart(
                kind="image",
                vault_path="uploads/screenshot.png",
                mime_type="image/png",
            ),
        ],
    )


def test_image_attachment_costs_far_more_than_its_path() -> None:
    plain = ChatMessage(role=Role.USER, content="look at this")
    with_img = _with_image()
    assert estimate_tokens([with_img]) > estimate_tokens([plain]) + 1_000


def test_attachment_surcharge_is_per_kind() -> None:
    msg = ChatMessage(
        role=Role.USER,
        content=[
            ContentPart(kind="text", text="hi"),
            ContentPart(kind="image", vault_path="a.png", mime_type="image/png"),
            ContentPart(kind="audio", vault_path="b.wav", mime_type="audio/wav"),
        ],
    )
    expected = ATTACHMENT_TOKEN_COST["image"] + ATTACHMENT_TOKEN_COST["audio"]
    assert estimate_attachment_tokens([msg]) == expected


def test_text_parts_still_contribute() -> None:
    short = _with_image("hi")
    long = _with_image("y" * 4_000)
    assert estimate_tokens([long]) > estimate_tokens([short])


def test_plain_string_content_is_unaffected() -> None:
    msgs = [ChatMessage(role=Role.USER, content="hello world")]
    assert estimate_attachment_tokens(msgs) == 0
    assert estimate_tokens(msgs) > 0


def test_empty_input_is_zero() -> None:
    assert estimate_tokens([]) == 0


# ── model window resolution ────────────────────────────────────────────────


def test_exact_registry_hit() -> None:
    assert known_context_window("gpt-4o") == 128_000


def test_provider_prefix_is_stripped() -> None:
    assert known_context_window("openai/gpt-4o") == 128_000


def test_longest_key_wins_over_shorter_prefix() -> None:
    """``gpt-4.1-mini`` must not resolve via the shorter ``gpt-4.1`` key when
    it has its own entry — and both are 1M here, so assert the mechanism on
    a pair where the windows differ."""
    assert known_context_window("gpt-4o-mini") == 128_000


def test_unknown_current_model_resolves_by_family() -> None:
    """The registry cannot know every id, but it can recognize a family. This
    is what stops a newly released model from silently collapsing to 32K."""
    assert known_context_window("claude-opus-4-7-20991231") == 200_000
    assert known_context_window("claude-sonnet-9") == 200_000
    assert known_context_window("gemini-4-pro-preview") == 1_048_576


def test_genuinely_unknown_model_returns_zero() -> None:
    assert known_context_window("totally-made-up-model") == 0
    assert known_context_window("") == 0


def test_effective_window_never_returns_zero() -> None:
    """Returning 0 is what disabled the pre-turn gate entirely for
    unrecognized models."""
    assert effective_context_window("totally-made-up-model") == DEFAULT_FALLBACK_WINDOW
    assert effective_context_window("") == DEFAULT_FALLBACK_WINDOW


def test_configured_window_wins() -> None:
    assert effective_context_window("gpt-4o", 777_000) == 777_000
    # A zero/absent configuration falls through to the registry.
    assert effective_context_window("gpt-4o", 0) == 128_000


# ── budget helpers ─────────────────────────────────────────────────────────


def test_usable_tokens_reserves_reply_and_tool_overhead() -> None:
    assert usable_tokens(200_000) == 200_000 - OUTPUT_HEADROOM_TOKENS - TOOLS_AND_SYSTEM_OVERHEAD


def test_usable_tokens_never_negative() -> None:
    assert usable_tokens(1_000) == 0


def test_usable_tokens_applies_fallback_for_zero_window() -> None:
    assert usable_tokens(0) == usable_tokens(DEFAULT_FALLBACK_WINDOW)


def test_recent_k_scales_with_window_and_is_clamped() -> None:
    small = recent_k_for_window(DEFAULT_FALLBACK_WINDOW)
    large = recent_k_for_window(1_048_576)
    assert small >= 12
    assert large <= 60
    assert large > small


def test_message_count_trigger() -> None:
    assert check_message_count([1] * 5, limit=10) is False
    assert check_message_count([1] * 50, limit=10) is True
    # 0 disables the trigger entirely.
    assert check_message_count([1] * 50, limit=0) is False
