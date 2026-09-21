"""Tests for the ``page`` tool — the ask_user-shaped round-trip that
lets the agent act on the user's real browser tab via the Chrome side
panel.

Covers: happy path (event published + future resolved through the same
``resolve_pending`` the /respond endpoint calls), non-JSON answers,
timeout path (cancels the pending future, publishes
page_request_cancelled), invalid action, and missing session context.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from nexus.agent.context import CURRENT_SESSION_ID
from nexus.agent.page_tool import PAGE_TOOL, PageHandler
from nexus.server.session_store import SessionStore


def _handler(tmp_path: Path) -> tuple[SessionStore, PageHandler]:
    store = SessionStore(db_path=tmp_path / "sessions.sqlite")
    return store, PageHandler(session_store=store)


def _bind_session(store: SessionStore) -> tuple[str, object]:
    s = store.create()
    token = CURRENT_SESSION_ID.set(s.id)
    return s.id, token


async def _responder(store: SessionStore, sid: str, answer: str) -> asyncio.Task:
    """Consume the session stream; on page_request resolve with `answer`."""

    async def run() -> None:
        async for ev in store.subscribe(sid):
            if ev.kind == "page_request":
                store.resolve_pending(sid, ev.data["request_id"], answer)
                return

    task = asyncio.create_task(run())
    await asyncio.sleep(0)
    return task


async def test_page_tool_spec_shape() -> None:
    assert PAGE_TOOL.name == "page"
    assert "action" in PAGE_TOOL.parameters["properties"]
    assert PAGE_TOOL.parameters["required"] == ["action"]
    assert "transcript" in PAGE_TOOL.parameters["properties"]["action"]["enum"]
    assert "tab" in PAGE_TOOL.parameters["properties"]
    assert "mode" in PAGE_TOOL.parameters["properties"]


async def test_page_params_passthrough(tmp_path: Path) -> None:
    store, handler = _handler(tmp_path)
    sid, token = _bind_session(store)
    try:
        seen: dict = {}

        async def run() -> None:
            async for ev in store.subscribe(sid):
                if ev.kind == "page_request":
                    seen.update(ev.data)
                    store.resolve_pending(sid, ev.data["request_id"], '{"ok": true}')
                    return

        consumer = asyncio.create_task(run())
        await asyncio.sleep(0)
        await handler.invoke(
            {"action": "transcript", "lang": "pt", "tab": "dallagnol"}
        )
        await asyncio.wait_for(consumer, timeout=2.0)
        assert seen["action"] == "transcript"
        assert seen["params"] == {"lang": "pt", "tab": "dallagnol"}
    finally:
        CURRENT_SESSION_ID.reset(token)


async def test_page_happy_path_round_trip(tmp_path: Path) -> None:
    store, handler = _handler(tmp_path)
    sid, token = _bind_session(store)
    try:
        consumer = await _responder(
            store, sid, json.dumps({"ok": True, "clicked": "#submit"})
        )
        result = json.loads(
            await handler.invoke({"action": "click", "selector": "#submit"})
        )
        await asyncio.wait_for(consumer, timeout=2.0)
        assert result == {"ok": True, "clicked": "#submit"}
    finally:
        CURRENT_SESSION_ID.reset(token)


async def test_page_non_json_answer_wrapped(tmp_path: Path) -> None:
    store, handler = _handler(tmp_path)
    sid, token = _bind_session(store)
    try:
        consumer = await _responder(store, sid, "plain text answer")
        result = json.loads(await handler.invoke({"action": "scroll", "dy": 300}))
        await asyncio.wait_for(consumer, timeout=2.0)
        assert result == {"ok": True, "result": "plain text answer"}
    finally:
        CURRENT_SESSION_ID.reset(token)


async def test_page_timeout_cancels_and_publishes(tmp_path: Path) -> None:
    store, handler = _handler(tmp_path)
    sid, token = _bind_session(store)
    try:
        seen: list[str] = []

        async def watch() -> None:
            async for ev in store.subscribe(sid):
                if ev.kind.startswith("page_"):
                    seen.append(ev.kind)
                    if ev.kind == "page_request_cancelled":
                        return

        watcher = asyncio.create_task(watch())
        await asyncio.sleep(0)

        result = json.loads(await handler.invoke({"action": "read", "timeout": 5}))
        assert result["ok"] is False
        assert "side panel" in result["error"]

        await asyncio.wait_for(watcher, timeout=2.0)
        assert "page_request" in seen
        assert "page_request_cancelled" in seen
    finally:
        CURRENT_SESSION_ID.reset(token)


async def test_page_invalid_action(tmp_path: Path) -> None:
    _, handler = _handler(tmp_path)
    result = json.loads(await handler.invoke({"action": "explode"}))
    assert result["ok"] is False
    assert "unknown action" in result["error"]


async def test_page_missing_session_context(tmp_path: Path) -> None:
    _, handler = _handler(tmp_path)
    assert CURRENT_SESSION_ID.get() is None
    result = json.loads(await handler.invoke({"action": "read"}))
    assert result["ok"] is False
    assert "no session context" in result["error"]
