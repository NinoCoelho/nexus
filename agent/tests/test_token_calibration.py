"""Estimator calibration from the provider's reported input size.

The token estimate is a chars/token heuristic, but every turn's response
carries the provider's *actual* input token count. That number was read only
for an empty-response diagnostic and then thrown away, so estimator drift
(dense languages, unusual tokenizers, tool schemas above the flat 12K
assumption) silently ate the safety margin.

The correction is deliberately one-directional: it can pull the soft
auto-compact trigger earlier, never later. Under-compacting costs the user a
turn; over-compacting costs a summary.
"""

from __future__ import annotations

import pytest

from nexus.agent.llm import ChatMessage, Role
from nexus.agent.loop.agent import Agent
from nexus.agent.loop.overflow import TOOLS_AND_SYSTEM_OVERHEAD, estimate_tokens


def _agent() -> Agent:
    """An Agent shell with just the calibration state initialized.

    ``__init__`` builds a provider, a loom agent and a tool registry; none of
    that is involved in the calibration arithmetic.
    """
    a = Agent.__new__(Agent)
    a._token_calibration = {}
    a._soft_compact_watermark = {}
    return a


def _history() -> list[ChatMessage]:
    return [
        ChatMessage(role=Role.USER, content="hello there"),
        ChatMessage(role=Role.ASSISTANT, content="general kenobi"),
    ]


def _modelled(history: list[ChatMessage]) -> int:
    return estimate_tokens(history) + TOOLS_AND_SYSTEM_OVERHEAD


def test_default_factor_is_neutral() -> None:
    a = _agent()
    assert a._calibration_for("s1") == 1.0
    assert a._calibration_for(None) == 1.0
    assert a._calibration_for("") == 1.0


def test_undercounting_raises_the_factor() -> None:
    a = _agent()
    history = _history()
    # The provider charged twice what we modelled.
    a._record_token_calibration("s1", _modelled(history) * 2, history)
    assert a._calibration_for("s1") > 1.0


def test_factor_never_drops_below_one() -> None:
    """Over-counting must not relax the budget — that would hand back the
    safety margin the heuristic exists to provide."""
    a = _agent()
    history = _history()
    a._record_token_calibration("s1", max(1, _modelled(history) // 4), history)
    assert a._calibration_for("s1") == 1.0


def test_factor_is_capped() -> None:
    a = _agent()
    history = _history()
    a._record_token_calibration("s1", _modelled(history) * 100, history)
    assert a._calibration_for("s1") <= Agent._CALIBRATION_MAX


def test_repeated_observations_smooth_toward_the_ratio() -> None:
    a = _agent()
    history = _history()
    target = _modelled(history) * 2
    for _ in range(10):
        a._record_token_calibration("s1", target, history)
    # EMA of a constant observation converges on it (clamped at the cap).
    assert a._calibration_for("s1") == pytest.approx(Agent._CALIBRATION_MAX)


def test_missing_or_zero_usage_is_ignored() -> None:
    a = _agent()
    history = _history()
    a._record_token_calibration("s1", 0, history)
    a._record_token_calibration(None, 99_999, history)
    a._record_token_calibration("", 99_999, history)
    assert a._token_calibration == {}


def test_empty_working_messages_is_safe() -> None:
    a = _agent()
    # Overhead alone keeps ``modelled`` positive, so this records rather than
    # dividing by zero.
    a._record_token_calibration("s1", 1_000, [])
    assert a._calibration_for("s1") == 1.0


def test_calibration_map_is_bounded() -> None:
    a = _agent()
    history = _history()
    actual = _modelled(history) * 2
    for i in range(Agent._WATERMARK_MAX_ENTRIES + 50):
        a._record_token_calibration(f"s{i}", actual, history)
    assert len(a._token_calibration) <= Agent._WATERMARK_MAX_ENTRIES


def test_watermark_map_is_bounded_and_ignores_sessionless_turns() -> None:
    a = _agent()
    for i in range(Agent._WATERMARK_MAX_ENTRIES + 50):
        a._remember_watermark(f"s{i}", 1_000 + i)
    assert len(a._soft_compact_watermark) <= Agent._WATERMARK_MAX_ENTRIES

    # Sessionless turns must not share one bucket — they used to collide on
    # the "" key and suppress each other's compaction.
    a._remember_watermark(None, 5_000)
    a._remember_watermark("", 5_000)
    assert a._watermark_for(None) == 0
    assert a._watermark_for("") == 0


def test_watermark_only_grows() -> None:
    a = _agent()
    a._remember_watermark("s1", 5_000)
    a._remember_watermark("s1", 1_000)
    assert a._watermark_for("s1") == 5_000
