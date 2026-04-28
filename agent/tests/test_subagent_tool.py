"""Tests for the spawn_subagents wiring on the nexus side.

The tool spec, handler validation, and runner protocol live in loom
(see ``loom.tools.subagent`` + ``loom/tests/test_subagent_tool.py``).
This file covers nexus-only concerns: the SessionStore additions
(``create_child`` + ``hidden`` filter) that the runner relies on to
persist child sessions without polluting the user's session list.
"""

from __future__ import annotations

from pathlib import Path

from nexus.agent.llm import ChatMessage, Role
from nexus.server.session_store import SessionStore


def _store(tmp_path: Path) -> SessionStore:
    return SessionStore(db_path=tmp_path / "sessions.sqlite")


def test_create_child_links_parent_and_hides(tmp_path: Path) -> None:
    store = _store(tmp_path)
    parent = store.create()
    child = store.create_child(parent_session_id=parent.id, hidden=True)

    row = store._loom._db.execute(
        "SELECT parent_session_id, hidden FROM sessions WHERE id = ?",
        (child.id,),
    ).fetchone()
    assert row is not None
    assert row[0] == parent.id
    assert row[1] == 1


def test_list_excludes_hidden_by_default(tmp_path: Path) -> None:
    store = _store(tmp_path)
    parent = store.create()
    store.replace_history(parent.id, [ChatMessage(role=Role.USER, content="hi")])

    store.create_child(parent_session_id=parent.id, hidden=True)
    store.create_child(parent_session_id=parent.id, hidden=True)

    visible = store.list()
    assert [s.id for s in visible] == [parent.id]


def test_list_include_hidden_returns_children(tmp_path: Path) -> None:
    store = _store(tmp_path)
    parent = store.create()
    c1 = store.create_child(parent_session_id=parent.id, hidden=True)
    c2 = store.create_child(parent_session_id=parent.id, hidden=True)

    all_ids = {s.id for s in store.list(include_hidden=True)}
    assert all_ids == {parent.id, c1.id, c2.id}
