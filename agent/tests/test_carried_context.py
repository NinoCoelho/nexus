"""Carried-context SYSTEM messages must survive the pre-call prompt rebuild.

``_builder._before_llm_call`` rebuilds the system prompt on every LLM call and
drops the history's SYSTEM messages so stale prompts from earlier turns don't
stack. Compaction, however, stores its session-memory summary as a SYSTEM
message — so that strip also deleted the summary. The history shrank and the
model never received the compressed version: from the model's side,
summarization was indistinguishable from silent truncation.

``is_carried_context`` is the predicate that tells the two apart. The builder
folds matching messages into the single built prompt rather than passing them
through, because the providers disagree on how to handle more than one SYSTEM
message (the Anthropic encoder keeps only the last).
"""

from __future__ import annotations

from nexus.agent.loop.compact import is_carried_context
from nexus.agent.loop.summarize import _SUMMARY_PREFIX


def test_session_memory_summary_is_carried_context() -> None:
    content = f"{_SUMMARY_PREFIX} — auto-generated summary]\n## Session Memory\n- Goals: x"
    assert is_carried_context(content) is True


def test_seeded_resume_note_is_carried_context() -> None:
    """The shape ``Agent._seed_session_notes`` injects on resume."""
    assert is_carried_context("[Session Memory — carried over from chat abc]\nnotes")


def test_elision_marker_is_carried_context() -> None:
    assert is_carried_context("[nx:elided] 12 older message(s) were removed") is True


def test_leading_whitespace_tolerated() -> None:
    assert is_carried_context("\n  [Session Memory] x") is True


def test_a_system_prompt_is_not_carried_context() -> None:
    """The built prompt must still be dropped, or prompts stack every turn."""
    assert is_carried_context("You are Nexus, a self-evolving agent...") is False


def test_non_string_and_empty_are_not_carried_context() -> None:
    assert is_carried_context(None) is False
    assert is_carried_context("") is False
    assert is_carried_context([]) is False
    assert is_carried_context(123) is False


def test_summary_prefix_constants_agree() -> None:
    """If ``_SUMMARY_PREFIX`` is ever changed, the predicate must follow or
    summaries start getting dropped again."""
    from nexus.agent.loop.compact import _CARRIED_CONTEXT_PREFIXES

    assert any(_SUMMARY_PREFIX.startswith(p) for p in _CARRIED_CONTEXT_PREFIXES)
