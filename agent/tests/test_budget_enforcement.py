"""Tests for budget enforcement — hard stop and cross-turn cumulative budget.

Verifies that:
- check_tool_budget correctly tracks cumulative tokens and call counts
- estimate_session_tool_tokens sums TOOL-role messages only
- Budget exceeded produces the correct hint text
"""

from __future__ import annotations

from nexus.agent.llm.types import ChatMessage, Role
from nexus.agent.loop.budget import (
    BUDGET_EXCEEDED_HINT,
    CALL_LIMIT_EXCEEDED_HINT,
    DEFAULT_SESSION_TOOL_BUDGET_TOKENS,
    check_tool_budget,
    estimate_session_tool_tokens,
)


def test_budget_not_exceeded_under_threshold() -> None:
    bc = check_tool_budget(
        cumulative_tool_tokens=0,
        new_result_text="x" * 400,
        budget=1000,
    )
    assert not bc.exceeded
    assert bc.cumulative_tool_tokens > 0


def test_budget_exceeded_over_threshold() -> None:
    bc = check_tool_budget(
        cumulative_tool_tokens=900,
        new_result_text="x" * 1200,
        budget=1000,
    )
    assert bc.exceeded


def test_call_limit_exceeded() -> None:
    call_counts: dict[str, int] = {}
    bc = check_tool_budget(
        cumulative_tool_tokens=0,
        new_result_text="ok",
        budget=100_000,
        call_counts=call_counts,
        tool_name="web_scrape",
        call_limits={"web_scrape": 3},
    )
    assert not bc.exceeded
    assert call_counts["web_scrape"] == 1

    for _ in range(3):
        bc = check_tool_budget(
            cumulative_tool_tokens=0,
            new_result_text="ok",
            budget=100_000,
            call_counts=call_counts,
            tool_name="web_scrape",
            call_limits={"web_scrape": 3},
        )
    assert bc.exceeded
    assert bc.call_limit_exceeded == ("web_scrape", 4)


def test_zero_budget_disables_check() -> None:
    bc = check_tool_budget(
        cumulative_tool_tokens=0,
        new_result_text="x" * 100_000,
        budget=0,
    )
    assert not bc.exceeded


def test_estimate_session_tool_tokens_counts_only_tools() -> None:
    history = [
        ChatMessage(role=Role.USER, content="x" * 10_000),
        ChatMessage(role=Role.ASSISTANT, content="y" * 10_000),
        ChatMessage(role=Role.TOOL, content="z" * 4_000, tool_call_id="t1", name="web_search"),
        ChatMessage(role=Role.TOOL, content="w" * 8_000, tool_call_id="t2", name="web_scrape"),
    ]
    tokens = estimate_session_tool_tokens(history)
    assert tokens > 0
    tool_only_chars = 4_000 + 8_000
    non_tool_chars = 10_000 + 10_000
    assert tokens < (tool_only_chars + non_tool_chars) // 3


def test_estimate_session_tool_tokens_empty_history() -> None:
    assert estimate_session_tool_tokens([]) == 0


def test_estimate_session_tool_tokens_no_tools() -> None:
    history = [
        ChatMessage(role=Role.USER, content="x" * 10_000),
        ChatMessage(role=Role.ASSISTANT, content="y" * 10_000),
    ]
    assert estimate_session_tool_tokens(history) == 0


def test_budget_exceeded_hint_is_critical() -> None:
    assert "CRITICAL" in BUDGET_EXCEEDED_HINT
    assert "MUST NOT" in BUDGET_EXCEEDED_HINT


def test_call_limit_hint_formats_correctly() -> None:
    formatted = CALL_LIMIT_EXCEEDED_HINT.format(count=3, tool="web_scrape")
    assert "3 web_scrape" in formatted
    assert "CRITICAL" in formatted


def test_default_session_budget_is_50k() -> None:
    assert DEFAULT_SESSION_TOOL_BUDGET_TOKENS == 50_000
