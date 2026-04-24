"""Tests for SessionStore.persist_partial_turn — the recovery path that
writes a partial assistant message + tool badges when the stream aborts
before emitting ``done``."""

from __future__ import annotations

from pathlib import Path

from nexus.agent.llm import ChatMessage, Role
from nexus.server.session_store import SessionStore


def test_persist_partial_turn_writes_user_and_partial_assistant(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "sessions.sqlite")
    sess = store.create()

    store.persist_partial_turn(
        sess.id,
        base_history=[],
        user_message="do a thing",
        assistant_text="I started doing",
        tool_calls=[
            {"id": "t1", "name": "vault_tool", "args": {"action": "read"}, "status": "done", "result_preview": "ok"},
            {"id": "t2", "name": "http_call", "args": {"url": "x"}, "status": "pending"},
        ],
        status_note="interrupted",
    )

    reloaded = store.get(sess.id)
    assert reloaded is not None
    # Two messages: user + assistant (tool-result messages are not synthesised
    # here — the server-side partial writer only preserves the assistant turn
    # and its tool_call stubs so the UI can render badges).
    assert [m.role for m in reloaded.history] == [Role.USER, Role.ASSISTANT]
    user_msg, asst_msg = reloaded.history
    assert user_msg.content == "do a thing"
    assert asst_msg.content.startswith("[interrupted] ")
    assert "I started doing" in asst_msg.content
    assert asst_msg.tool_calls is not None
    names = [tc.name for tc in asst_msg.tool_calls]
    assert names == ["vault_tool", "http_call"]
    assert asst_msg.tool_calls[0].arguments == {"action": "read"}


def test_persist_partial_turn_preserves_base_history(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "sessions.sqlite")
    sess = store.create()
    base = [
        ChatMessage(role=Role.USER, content="prior turn"),
        ChatMessage(role=Role.ASSISTANT, content="prior reply"),
    ]
    store.replace_history(sess.id, base)

    store.persist_partial_turn(
        sess.id,
        base_history=base,
        user_message="second turn",
        assistant_text="",
        tool_calls=[{"id": "a", "name": "memory_tool", "args": {}}],
        status_note="cancelled",
    )

    reloaded = store.get(sess.id)
    assert reloaded is not None
    assert len(reloaded.history) == 4
    assert reloaded.history[2].content == "second turn"
    assert reloaded.history[3].tool_calls is not None
    assert reloaded.history[3].tool_calls[0].name == "memory_tool"


def test_persist_partial_turn_noop_when_nothing_happened(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "sessions.sqlite")
    sess = store.create()

    store.persist_partial_turn(
        sess.id,
        base_history=[],
        user_message="",
        assistant_text="",
        tool_calls=[],
    )
    reloaded = store.get(sess.id)
    assert reloaded is not None
    assert reloaded.history == []
