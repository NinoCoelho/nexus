"""Tests for the spawn_subagents agent tool.

Two layers:
- Handler-level: shape of the JSON returned for each guard rail and the
  happy path with a stub runner.
- SessionStore-level: ``create_child`` persists a hidden child session and
  ``list`` filters it out by default.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus.agent.llm import ChatMessage, Role
from nexus.server.session_store import SessionStore
from nexus.tools.subagent_tool import (
    MAX_SUBAGENT_DEPTH,
    MAX_TASKS_PER_CALL,
    handle_spawn_subagents,
)


# ── handler guard rails ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_runner_not_wired() -> None:
    out = json.loads(await handle_spawn_subagents(
        {"tasks": [{"prompt": "x"}]},
        runner=None, parent_session_id="p1", depth=0,
    ))
    assert out["ok"] is False
    assert "runner not wired" in out["error"]


@pytest.mark.asyncio
async def test_no_parent_session_id() -> None:
    async def stub(*_a, **_kw):
        return []
    out = json.loads(await handle_spawn_subagents(
        {"tasks": [{"prompt": "x"}]},
        runner=stub, parent_session_id=None, depth=0,
    ))
    assert out["ok"] is False
    assert "session" in out["error"].lower()


@pytest.mark.asyncio
async def test_depth_limit_exceeded() -> None:
    async def stub(*_a, **_kw):
        return []
    out = json.loads(await handle_spawn_subagents(
        {"tasks": [{"prompt": "x"}]},
        runner=stub, parent_session_id="p1", depth=MAX_SUBAGENT_DEPTH,
    ))
    assert out["ok"] is False
    assert "depth limit" in out["error"]


@pytest.mark.asyncio
async def test_empty_tasks_rejected() -> None:
    async def stub(*_a, **_kw):
        return []
    out = json.loads(await handle_spawn_subagents(
        {"tasks": []},
        runner=stub, parent_session_id="p1", depth=0,
    ))
    assert out["ok"] is False
    assert "non-empty" in out["error"]


@pytest.mark.asyncio
async def test_too_many_tasks_rejected() -> None:
    async def stub(*_a, **_kw):
        return []
    out = json.loads(await handle_spawn_subagents(
        {"tasks": [{"prompt": f"q{i}"} for i in range(MAX_TASKS_PER_CALL + 1)]},
        runner=stub, parent_session_id="p1", depth=0,
    ))
    assert out["ok"] is False
    assert "too many" in out["error"]


@pytest.mark.asyncio
async def test_missing_prompt_rejected() -> None:
    async def stub(*_a, **_kw):
        return []
    out = json.loads(await handle_spawn_subagents(
        {"tasks": [{"prompt": "ok"}, {"name": "broken"}]},
        runner=stub, parent_session_id="p1", depth=0,
    ))
    assert out["ok"] is False
    assert "task[1]" in out["error"]


@pytest.mark.asyncio
async def test_runner_crash_surfaces_as_error() -> None:
    async def boom(*_a, **_kw):
        raise RuntimeError("kaboom")
    out = json.loads(await handle_spawn_subagents(
        {"tasks": [{"prompt": "x"}]},
        runner=boom, parent_session_id="p1", depth=0,
    ))
    assert out["ok"] is False
    assert "kaboom" in out["error"]


# ── handler happy path ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_runner_results_shaped_per_task() -> None:
    captured: dict = {}

    async def stub_runner(tasks, *, parent_session_id, depth):
        captured["tasks"] = tasks
        captured["parent_session_id"] = parent_session_id
        captured["depth"] = depth
        return [
            {"session_id": f"child{i}", "result": f"answer-{i}", "error": None}
            for i, _ in enumerate(tasks)
        ]

    args = {"tasks": [
        {"name": "a", "prompt": "research A"},
        {"name": "b", "prompt": "research B"},
        {"name": "c", "prompt": "research C"},
    ]}
    out = json.loads(await handle_spawn_subagents(
        args, runner=stub_runner, parent_session_id="parent-xyz", depth=0,
    ))

    assert out["ok"] is True
    assert len(out["results"]) == 3
    assert [r["name"] for r in out["results"]] == ["a", "b", "c"]
    assert [r["result"] for r in out["results"]] == ["answer-0", "answer-1", "answer-2"]
    assert all(r["session_id"].startswith("child") for r in out["results"])
    assert all(r["error"] is None for r in out["results"])

    # Runner received the tasks + propagated context
    assert captured["parent_session_id"] == "parent-xyz"
    assert captured["depth"] == 0
    assert len(captured["tasks"]) == 3


@pytest.mark.asyncio
async def test_per_task_error_passes_through() -> None:
    async def stub_runner(tasks, *, parent_session_id, depth):
        return [
            {"session_id": "c0", "result": "fine", "error": None},
            {"session_id": "c1", "result": "", "error": "subagent crashed"},
        ]

    out = json.loads(await handle_spawn_subagents(
        {"tasks": [{"prompt": "ok"}, {"prompt": "doomed"}]},
        runner=stub_runner, parent_session_id="p", depth=0,
    ))
    assert out["ok"] is True
    assert out["results"][0]["error"] is None
    assert out["results"][1]["error"] == "subagent crashed"


# ── SessionStore: create_child + list filtering ──────────────────────────────


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
    # Give parent a message so it has a non-empty footprint, mirroring real use.
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
