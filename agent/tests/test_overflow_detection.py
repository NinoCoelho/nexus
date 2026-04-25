"""Pre-flight + post-flight context overflow detection.

Pins down the behavior added so a session whose persisted history blew past
the model's window stops looping on `empty_response` and instead surfaces a
structured `context_overflow` error with a `compact_history` action.
"""

from __future__ import annotations

from nexus.agent.loop.overflow import check_overflow, estimate_tokens


class _Msg:
    def __init__(self, content: str, tool_calls=None) -> None:
        self.content = content
        self.tool_calls = tool_calls


def test_estimate_tokens_handles_strings_and_tool_calls() -> None:
    msgs = [
        _Msg("hello world"),
        _Msg("", tool_calls=[{"id": "a", "name": "f", "arguments": "{}"}]),
    ]
    n = estimate_tokens(msgs)
    # Cheap chars/4 estimator with per-message overhead — exact value is not
    # the contract, only that it's positive and grows with content.
    assert n > 0
    assert estimate_tokens(msgs + [_Msg("x" * 400)]) > n + 80


def test_check_overflow_skips_when_window_unknown() -> None:
    out = check_overflow([_Msg("x" * 10_000_000)], context_window=0)
    assert out.overflowed is False
    assert out.context_window == 0


def test_check_overflow_clears_with_room_to_spare() -> None:
    out = check_overflow([_Msg("hello")], context_window=200_000)
    assert out.overflowed is False
    assert out.estimated_input_tokens < 100


def test_check_overflow_flags_oversized_history() -> None:
    # 1.4 MB of content ≈ 350K tokens with chars/4 → blows past a 200K window.
    big_msg = _Msg("x" * 1_400_000)
    out = check_overflow([big_msg], context_window=200_000)
    assert out.overflowed is True
    assert out.estimated_input_tokens > 200_000
    assert "Compact" in (out.detail or "") or "compact" in (out.detail or "")


def test_check_overflow_respects_output_headroom() -> None:
    # Just under the window but inside the headroom: still flagged because
    # the model would have no room to reply.
    msg = _Msg("x" * (4 * 199_500))  # ≈ 199.5K tokens
    out = check_overflow([msg], context_window=200_000, output_headroom=2_000)
    assert out.overflowed is True
