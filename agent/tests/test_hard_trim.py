"""Deterministic last-resort trimming — the guaranteed-fit contract.

``hard_trim`` is the backstop that makes "ran out of context" unreachable.
Everything above it may legitimately decline to act: ``auto_compact`` only
rewrites TOOL messages, and summarization needs a working LLM, so a history
made mostly of user/assistant prose used to be unshrinkable — the turn died
with an overflow error and the session was permanently stuck.

These tests pin the properties that make it a guarantee rather than a
best-effort: it always fits, it never splits a tool pair, and it never drops
the system prompt or the user's actual question.
"""

from __future__ import annotations

import pytest

from nexus.agent.llm import ChatMessage, Role, ToolCall
from nexus.agent.loop.compact import _estimate_for, hard_trim


@pytest.fixture(autouse=True)
def _isolate_artifact_dirs(tmp_path, monkeypatch):
    """Redirect the tool cache and recovery archive into tmp_path.

    The suite's conftest does not relocate ``~/.nexus``, and trimming writes
    the full text of everything it shrinks to disk — without this, running
    these tests would litter the developer's real vault.
    """
    import nexus.agent.loop.compact as compact_mod
    import nexus.agent.loop.summarize as summarize_mod

    monkeypatch.setattr(compact_mod, "_vault_tool_cache_fn", lambda: tmp_path / ".tool-cache")
    monkeypatch.setattr(summarize_mod, "_session_memory_fn", lambda: tmp_path)


def _sys(text: str = "system prompt") -> ChatMessage:
    return ChatMessage(role=Role.SYSTEM, content=text)


def _u(text: str) -> ChatMessage:
    return ChatMessage(role=Role.USER, content=text)


def _a(text: str) -> ChatMessage:
    return ChatMessage(role=Role.ASSISTANT, content=text)


def _a_call(call_id: str, name: str = "vault_read") -> ChatMessage:
    return ChatMessage(
        role=Role.ASSISTANT,
        content="",
        tool_calls=[ToolCall(id=call_id, name=name, arguments={"path": "a.md"})],
    )


def _t(call_id: str, text: str, name: str = "vault_read") -> ChatMessage:
    return ChatMessage(role=Role.TOOL, content=text, tool_call_id=call_id, name=name)


def test_noop_when_already_under_target() -> None:
    history = [_sys(), _u("hi"), _a("hello")]
    out, elided = hard_trim(history, target_tokens=100_000)
    assert elided == 0
    assert [m.content for m in out] == [m.content for m in history]


def test_zero_target_is_a_noop_not_a_wipe() -> None:
    history = [_sys(), _u("hi")]
    out, elided = hard_trim(history, target_tokens=0)
    assert elided == 0
    assert len(out) == len(history)


def test_stubs_tool_results_before_dropping_anything() -> None:
    """Stage 1 alone should rescue a history whose bulk is tool output."""
    history = [_sys(), _u("question")]
    for i in range(6):
        history.append(_a_call(f"c{i}"))
        history.append(_t(f"c{i}", "x" * 4_000))
    history.append(_u("latest question"))

    before = _estimate_for(history)
    out, elided = hard_trim(history, target_tokens=before // 3)

    # Nothing had to be removed — the tool results were enough to shrink.
    assert elided == 0
    assert len(out) == len(history)
    assert _estimate_for(out) <= before // 3
    # Every tool message still answers its call.
    assert [m.tool_call_id for m in out if m.role == Role.TOOL] == [
        f"c{i}" for i in range(6)
    ]


def test_forces_a_fit_on_prose_only_history() -> None:
    """The case the old pipeline could not handle at all: no tool results,
    no summarizer. It must still come back under the target."""
    history = [_sys()]
    for i in range(40):
        history.append(_u(f"user turn {i} " + "y" * 3_000))
        history.append(_a(f"assistant turn {i} " + "z" * 3_000))
    history.append(_u("the current question"))

    target = 2_000
    out, _elided = hard_trim(history, target_tokens=target)
    assert _estimate_for(out) <= target


def test_preserves_system_prompt_and_last_user_message() -> None:
    history = [_sys("SYSTEM MARKER")]
    for i in range(30):
        history.append(_u(f"old {i} " + "y" * 3_000))
        history.append(_a(f"reply {i} " + "z" * 3_000))
    history.append(_u("THE CURRENT QUESTION"))

    out, _ = hard_trim(history, target_tokens=1_500)

    assert any(m.role == Role.SYSTEM and "SYSTEM MARKER" in (m.content or "") for m in out)
    users = [m for m in out if m.role == Role.USER]
    assert users, "the user's own message must survive"
    assert "THE CURRENT QUESTION" in (users[-1].content or "")


def test_never_orphans_a_tool_result() -> None:
    """A TOOL message without its assistant tool_calls (or vice versa) is
    rejected by the provider, so units must move together."""
    history = [_sys(), _u("start")]
    for i in range(20):
        history.append(_a_call(f"c{i}"))
        history.append(_t(f"c{i}", "x" * 3_000))
        history.append(_a(f"narration {i} " + "w" * 2_000))
    history.append(_u("current"))

    out, _ = hard_trim(history, target_tokens=1_200)

    call_ids = {
        tc.id
        for m in out
        if m.role == Role.ASSISTANT and m.tool_calls
        for tc in m.tool_calls
    }
    result_ids = {m.tool_call_id for m in out if m.role == Role.TOOL}
    assert result_ids <= call_ids, "every tool result still has its call"


def test_elision_leaves_a_marker_explaining_the_gap() -> None:
    history = [_sys()]
    for i in range(40):
        history.append(_u(f"old {i} " + "y" * 3_000))
        history.append(_a(f"reply {i} " + "z" * 3_000))
    history.append(_u("current"))

    out, elided = hard_trim(history, target_tokens=1_000)
    if elided:
        assert any("nx:elided" in (m.content or "") for m in out)


def test_elided_messages_are_archived(tmp_path) -> None:
    """Trimming is lossy for the window but must not be lossy for the record."""
    history = [_sys()]
    for i in range(40):
        history.append(_u(f"old {i} " + "y" * 3_000))
        history.append(_a(f"reply {i} " + "z" * 3_000))
    history.append(_u("current"))

    _out, elided = hard_trim(history, target_tokens=1_000, session_id="sess-trim")
    if elided:
        archive = tmp_path / ".parts" / "sess-trim.jsonl"
        assert archive.is_file()
        assert "hard_trim" in archive.read_text(encoding="utf-8")
