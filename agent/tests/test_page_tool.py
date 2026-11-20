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
from nexus.browser.manager import aggregate_nbie_events
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


async def test_page_tool_spec_new_actions() -> None:
    enum = PAGE_TOOL.parameters["properties"]["action"]["enum"]
    for action in (
        "find_element",
        "inspect_element",
        "viewport_info",
        "page_signature",
        "interactive_map",
        "screenshot",
        "extract",
    ):
        assert action in enum, action
    # every dispatchable action except fill_login (server-driven via
    # site_credentials) must be in the LLM-facing enum
    from nexus.agent.page_tool import _ACTIONS

    assert set(enum) == set(_ACTIONS) - {"fill_login"}, (set(enum), set(_ACTIONS))
    props = PAGE_TOOL.parameters["properties"]
    for prop in ("role", "aria_label", "tag", "visible", "limit", "question"):
        assert prop in props, prop


# ── NBIE telemetry (surface=ext) + read cache-hit ────────────────────────


async def test_page_read_cache_hit_flag_and_nbie_events(tmp_path: Path) -> None:
    from nexus.browser.manager import BrowserManager, set_manager

    mgr = BrowserManager()
    set_manager(mgr)
    try:
        store, handler = _handler(tmp_path)
        sid, token = _bind_session(store)
        try:
            task = await _responder(
                store, sid, '{"ok": true, "text": "hi", "contentHash": "h1"}'
            )
            r1 = json.loads(await handler.invoke({"action": "read"}))
            await asyncio.wait_for(task, timeout=2.0)
            assert r1["cache_hit"] is False  # first read is never a hit

            task = await _responder(
                store, sid, '{"ok": true, "text": "hi", "contentHash": "h1"}'
            )
            r2 = json.loads(await handler.invoke({"action": "read"}))
            await asyncio.wait_for(task, timeout=2.0)
            assert r2["cache_hit"] is True  # identical content → wasted spend

            assert len(mgr.nbie_events) == 2
            ev = mgr.nbie_events[-1]
            assert ev["surface"] == "ext"
            assert ev["action"] == "read" and ev["ok"] is True
            assert ev["level"] == "text"
            assert ev["key"].startswith("ext:")
            assert mgr.nbie_events[0]["cache_hit"] is False
            assert ev["cache_hit"] is True

            rep = aggregate_nbie_events(list(mgr.nbie_events))
            assert rep["by_surface"]["ext"]["actions"] == 2
            assert rep["by_surface"]["ext"]["cache_hits"] == 1
        finally:
            CURRENT_SESSION_ID.reset(token)
    finally:
        set_manager(None)


async def test_page_error_and_timeout_recorded(tmp_path: Path) -> None:
    from nexus.browser.manager import BrowserManager, set_manager

    mgr = BrowserManager()
    set_manager(mgr)
    try:
        store, handler = _handler(tmp_path)
        sid, token = _bind_session(store)
        try:
            task = await _responder(store, sid, '{"ok": false, "error": "boom"}')
            r = json.loads(await handler.invoke({"action": "read"}))
            await asyncio.wait_for(task, timeout=2.0)
            assert r["ok"] is False
            ev = mgr.nbie_events[-1]
            assert ev["ok"] is False and ev["error"] == "boom"
        finally:
            CURRENT_SESSION_ID.reset(token)
    finally:
        set_manager(None)


async def test_page_timeout_records_failure(tmp_path: Path, monkeypatch) -> None:
    from nexus.browser.manager import BrowserManager, set_manager

    mgr = BrowserManager()
    set_manager(mgr)
    try:
        store, handler = _handler(tmp_path)
        _sid, token = _bind_session(store)
        try:
            monkeypatch.setattr(
                "nexus.agent.page_tool._DEFAULT_TIMEOUT", 0.05, raising=False
            )
            r = json.loads(await handler.invoke({"action": "read", "timeout": 0.05}))
            assert r["ok"] is False and "no side panel" in r["error"]
            ev = mgr.nbie_events[-1]
            assert ev["ok"] is False and "timeout" in (ev.get("error") or "")
        finally:
            CURRENT_SESSION_ID.reset(token)
    finally:
        set_manager(None)


async def test_page_extract_subllm(tmp_path: Path) -> None:
    from nexus.browser import extract as page_extract
    from nexus.browser.manager import BrowserManager, set_manager

    async def fake_llm(prompt: str) -> str:
        assert "Portal page content here" in prompt
        return "the answer"

    page_extract.set_extract_llm(fake_llm)
    mgr = BrowserManager()
    set_manager(mgr)
    try:
        store, handler = _handler(tmp_path)
        sid, token = _bind_session(store)
        try:
            task = await _responder(
                store,
                sid,
                '{"ok": true, "text": "Portal page content here", "contentHash": "h9", '
                '"url": "https://x/", "title": "X"}',
            )
            out = json.loads(
                await handler.invoke({"action": "extract", "question": "what does it say?"})
            )
            await asyncio.wait_for(task, timeout=2.0)
            assert out["ok"] is True
            assert out["answer"] == "the answer"
            assert "text" not in out  # page text never enters the result
            assert out["contentHash"] == "h9"
            # the inner read was recorded as an ext read; extract as its own action
            actions = [e["action"] for e in mgr.nbie_events]
            assert "read" in actions and "extract" in actions

            out = json.loads(await handler.invoke({"action": "extract"}))
            assert out["ok"] is False and "question" in out["error"]
        finally:
            CURRENT_SESSION_ID.reset(token)
    finally:
        set_manager(None)
        page_extract.set_extract_llm(None)



async def test_page_find_element_refs_and_click_by_ref(tmp_path: Path) -> None:
    """find_element snapshots persist per session; click {ref} resolves to
    the element's selector (the native version of the data-nx-* tagging the
    agent hand-rolled in live sessions)."""
    store, handler = _handler(tmp_path)
    sid, token = _bind_session(store)
    try:
        find_answer = json.dumps(
            {
                "ok": True,
                "snapshotHash": "sh1",
                "elements": [
                    {"selector": "#add-permission", "ref": 1, "text": "Add a permission"},
                    {"selector": "#grant", "ref": 2, "text": "Grant admin consent"},
                ],
                "count": 2,
            }
        )
        seen_params: dict = {}

        async def run_find() -> None:
            async for ev in store.subscribe(sid):
                if ev.kind == "page_request":
                    seen_params.update(ev.data)
                    store.resolve_pending(sid, ev.data["request_id"], find_answer)
                    return

        task = asyncio.create_task(run_find())
        await asyncio.sleep(0)
        r = json.loads(await handler.invoke({"action": "find_element", "role": "button"}))
        await asyncio.wait_for(task, timeout=2.0)
        assert r["count"] == 2

        # click by ref → published as a plain selector click
        async def run_click() -> None:
            async for ev in store.subscribe(sid):
                if ev.kind == "page_request":
                    seen_params.update(ev.data)
                    store.resolve_pending(
                        sid, ev.data["request_id"], '{"ok": true, "clicked": "#add-permission"}'
                    )
                    return

        task = asyncio.create_task(run_click())
        await asyncio.sleep(0)
        r = json.loads(await handler.invoke({"action": "click", "ref": 1}))
        await asyncio.wait_for(task, timeout=2.0)
        assert r["ok"] is True
        assert seen_params["action"] == "click"
        assert seen_params["params"] == {"selector": "#add-permission"}  # ref resolved

        # stale/unknown ref → clear error, no panel round-trip needed
        r = json.loads(await handler.invoke({"action": "click", "ref": 99}))
        assert r["ok"] is False and "no element with ref=99" in r["error"]

        # no snapshot at all (fresh session) → actionable error
        sid2 = store.create().id
        tok2 = CURRENT_SESSION_ID.set(sid2)
        try:
            r = json.loads(await handler.invoke({"action": "click", "ref": 1}))
            assert r["ok"] is False and "find_element first" in r["error"]
        finally:
            CURRENT_SESSION_ID.reset(tok2)
    finally:
        CURRENT_SESSION_ID.reset(token)


async def test_page_save_flow_and_replay(tmp_path: Path, monkeypatch) -> None:
    """Steps taken in-session are saved (perception dropped) and replayed
    through the same invoke machinery, early-aborting on failure."""
    from nexus.browser import flows as flow_store

    fd = tmp_path / "flows"
    fd.mkdir()
    monkeypatch.setattr(flow_store, "flows_dir", lambda: fd)
    store, handler = _handler(tmp_path)
    sid, token = _bind_session(store)
    try:
        answers = [
            '{"ok": true, "text": "page", "contentHash": "h1"}',  # read (perception)
            '{"ok": true, "clicked": "#go", "url_changed": true}',  # click
            '{"ok": true, "clicked": "[data-x]", "state_changed": true}',  # click 2
        ]
        calls: list[str] = []

        async def responder() -> None:
            n = 0
            async for ev in store.subscribe(sid):
                if ev.kind == "page_request":
                    calls.append(ev.data["action"])
                    ans = answers[n] if n < len(answers) else '{"ok": true}'
                    if ev.data["action"] == "save_flow":
                        ans = None  # save_flow needs no panel round-trip
                    if ans is not None:
                        store.resolve_pending(sid, ev.data["request_id"], ans)
                    n += 1

        consumer = asyncio.create_task(responder())
        await asyncio.sleep(0)
        await handler.invoke({"action": "read"})
        await asyncio.wait_for(asyncio.sleep(0.2), timeout=1.0)
        await handler.invoke({"action": "click", "selector": "#go"})
        await asyncio.wait_for(asyncio.sleep(0.2), timeout=1.0)
        await handler.invoke({"action": "click", "selector": "[data-x]"})
        del consumer

        saved = json.loads(await handler.invoke({"action": "save_flow", "name": "grant-stuff"}))
        assert saved["ok"] is True and saved["saved"] == "grant-stuff"
        assert saved["steps_state_changing"] == 2  # read was dropped
        assert saved["steps_total"] == 3

        # replay: fresh session, same flow — two clicks re-executed
        sid2 = store.create().id
        tok2 = CURRENT_SESSION_ID.set(sid2)
        try:
            replay_answers = [
                '{"ok": true, "clicked": "#go", "url_changed": true}',
                '{"ok": false, "error": "element not found: [data-x]"}',
            ]

            async def responder2() -> None:
                n = 0
                async for ev in store.subscribe(sid2):
                    if ev.kind == "page_request":
                        ans = replay_answers[n] if n < len(replay_answers) else "{}"
                        store.resolve_pending(sid2, ev.data["request_id"], ans)
                        n += 1

            asyncio.create_task(responder2())
            await asyncio.sleep(0)
            r = json.loads(await handler.invoke({"action": "replay", "name": "grant-stuff"}))
            await asyncio.wait_for(asyncio.sleep(0.3), timeout=1.0)
            assert r["total"] == 2
            assert r["executed"] == 1  # first click ok, second failed → abort
            assert r["ok"] is False
            assert r["steps"][1]["ok"] is False and "element not found" in r["steps"][1]["error"]
        finally:
            CURRENT_SESSION_ID.reset(tok2)
    finally:
        CURRENT_SESSION_ID.reset(token)


async def test_page_replay_unknown_flow(tmp_path: Path, monkeypatch) -> None:
    from nexus.browser import flows as flow_store

    fd = tmp_path / "flows"
    fd.mkdir()
    monkeypatch.setattr(flow_store, "flows_dir", lambda: fd)
    store, handler = _handler(tmp_path)
    _sid, token = _bind_session(store)
    try:
        r = json.loads(await handler.invoke({"action": "replay", "name": "ghost"}))
        assert r["ok"] is False and "no saved flow" in r["error"]
        r = json.loads(await handler.invoke({"action": "flows"}))
        assert r["ok"] is True and r["flows"] == []
    finally:
        CURRENT_SESSION_ID.reset(token)


async def test_page_read_injects_site_notes(tmp_path: Path, monkeypatch) -> None:
    from nexus.browser import site_notes as sn

    d = tmp_path / "sites"
    d.mkdir()
    monkeypatch.setattr(sn, "sites_dir", lambda: d)
    (d / "admin.example.md").write_text("## Navigation\n- Apps > API permissions blade\n")
    store, handler = _handler(tmp_path)
    sid, token = _bind_session(store)
    try:
        task = await _responder(
            store, sid, '{"ok": true, "text": "t", "contentHash": "h1", "url": "https://admin.example/apps", "title": "Apps"}'
        )
        r = json.loads(await handler.invoke({"action": "read"}))
        await asyncio.wait_for(task, timeout=2.0)
        assert r["site_notes"]["excerpt"].startswith("## Navigation")
        assert r["site_notes"]["path"] == "vault://sites/admin.example.md"
    finally:
        CURRENT_SESSION_ID.reset(token)


async def test_page_episode_idle_triggers_distill(tmp_path: Path, monkeypatch) -> None:
    from nexus.browser import site_notes as sn

    captured: dict = {}

    async def fake_distill(host, steps, url="", title=""):
        captured["host"] = host
        captured["steps"] = steps

    monkeypatch.setattr(sn, "distill_and_save", fake_distill)
    import nexus.agent.page_tool as pt

    monkeypatch.setattr(pt, "EPISODE_IDLE_SECONDS", 0.0)  # every gap triggers
    store, handler = _handler(tmp_path)
    sid, token = _bind_session(store)
    try:
        # one state-changing step is below the threshold → no distill
        handler._episode_last[sid] = 1_000.0  # long ago
        task = await _responder(
            store, sid, '{"ok": true, "clicked": "#a", "url": "https://admin.example/", "title": "T"}'
        )
        await handler.invoke({"action": "click", "selector": "#a"})
        await asyncio.wait_for(task, timeout=2.0)
        await asyncio.sleep(0.05)
        assert "host" not in captured

        # three more state-changing steps, then a new "episode"
        for sel in ("#b", "#c", "#d"):
            task = await _responder(
                store, sid, f'{{"ok": true, "clicked": "{sel}", "url": "https://admin.example/", "title": "T"}}'
            )
            await handler.invoke({"action": "click", "selector": sel})
            await asyncio.wait_for(task, timeout=2.0)
        handler._episode_last[sid] = 1_000.0  # idle gap since last action
        task = await _responder(store, sid, '{"ok": true, "clicked": "#e", "url": "https://admin.example/", "title": "T"}')
        await handler.invoke({"action": "click", "selector": "#e"})
        await asyncio.wait_for(task, timeout=2.0)
        await asyncio.sleep(0.1)
        assert captured.get("host") == "https://admin.example/"
        assert len(captured["steps"]) >= 3  # the episode's steps went to the distiller
    finally:
        CURRENT_SESSION_ID.reset(token)
