"""Tests for the ``browser`` tool + BrowserManager (computer-use Chrome).

No real Chrome is launched: the manager's ensure/act plumbing is covered
via a stubbed BrowserInstance, and the handler is exercised against a
real SessionStore (context resolution: session → project key).

Covers: context-key resolution, action dispatch + JSON error envelope,
event emission on success/error (the live-view feed), screenshot
retention pruning, registry persistence, and unknown actions.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock

from nexus.agent.context import CURRENT_SESSION_ID
from nexus.browser.manager import (
    ACTIONS,
    BrowserError,
    BrowserInstance,
    BrowserManager,
    BrowserStillStarting,
    _prune_screenshots,
    _sanitize,
)
from nexus.browser.tool import BROWSER_TOOL, BrowserHandler
from nexus.server.session_store import SessionStore

# ── stubs ───────────────────────────────────────────────────────────────────


class StubInstance:
    """Records actions; succeeds unless the args say otherwise."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.calls: list[tuple[str, dict]] = []
        self.last_active = time.monotonic()
        self.last_page: dict = {}
        self.level_counts: dict[str, int] = {}

    async def ensure(self) -> None:
        return None

    async def act(self, action: str, args: dict) -> dict:
        self.calls.append((action, dict(args)))
        if args.get("boom"):
            raise BrowserError(f"stub failure for {action}")
        result = {"url": "https://example.com/", "title": "Example"}
        if action == "screenshot":
            result["screenshot"] = "/tmp/shot.png"
        return result

    def ws_open(self) -> bool:
        return True


class StubManager(BrowserManager):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[str, dict]] = []
        self.event_sink = lambda key, data: self.events.append((key, data))

    async def ensure(self, key: str) -> StubInstance:
        inst = getattr(self, "_stub", None)
        if inst is None or inst.key != key:
            inst = StubInstance(key)
            self._stub = inst
        return inst


def _published(store: SessionStore, sid: str, kind: str) -> list[dict]:
    """Drain the replay buffer for events of one kind."""
    with store._lock:
        return [ev.data for _seq, ev in store._replay.get(sid, ()) if ev.kind == kind]


# ── context resolution ──────────────────────────────────────────────────────


async def test_context_key_project_session(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "sessions.sqlite")
    manager = StubManager()
    handler = BrowserHandler(manager=manager, session_store=store)

    s = store.create(project_id="proj-42")
    token = CURRENT_SESSION_ID.set(s.id)
    try:
        assert handler.context_key() == "project:proj-42"
        result = json.loads(await handler.invoke({"action": "info"}))
        assert result["ok"] is True
        assert result["key"] == "project:proj-42"
        assert manager._stub.calls == [("info", {})]
    finally:
        CURRENT_SESSION_ID.reset(token)


async def test_context_key_unprojected_falls_back_to_main(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "sessions.sqlite")
    manager = StubManager()
    handler = BrowserHandler(manager=manager, session_store=store)

    s = store.create()
    token = CURRENT_SESSION_ID.set(s.id)
    try:
        assert handler.context_key() == "main"
    finally:
        CURRENT_SESSION_ID.reset(token)


async def test_context_key_without_session_context(tmp_path: Path) -> None:
    manager = StubManager()
    handler = BrowserHandler(manager=manager, session_store=None)
    assert CURRENT_SESSION_ID.get() is None
    assert handler.context_key() == "main"


# ── dispatch + error envelope ───────────────────────────────────────────────


async def test_unknown_action() -> None:
    manager = StubManager()
    handler = BrowserHandler(manager=manager)
    result = json.loads(await handler.invoke({"action": "explode"}))
    assert result["ok"] is False
    assert "unknown action" in result["error"]


async def test_browser_error_envelope() -> None:
    manager = StubManager()
    handler = BrowserHandler(manager=manager)
    result = json.loads(await handler.invoke({"action": "navigate", "boom": True}))
    assert result["ok"] is False
    assert result["error"].startswith("browser error: stub failure for navigate")
    assert result["key"] == "main"


async def test_spec_shape() -> None:
    assert BROWSER_TOOL.name == "browser"
    props = BROWSER_TOOL.parameters["properties"]
    # Tool-facing enum = manager ACTIONS + the tool-layer extract (sub-LLM)
    # + flow persistence (save_flow/replay/flows).
    assert props["action"]["enum"] == list(ACTIONS) + ["extract", "save_flow", "replay", "flows"]
    assert BROWSER_TOOL.parameters["required"] == ["action"]
    for p in ("url", "selector", "text", "x", "y", "delta_y", "expression", "timeout_seconds"):
        assert p in props, p


# ── event emission (live view feed) ─────────────────────────────────────────


async def test_success_and_error_emit_events(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "sessions.sqlite")
    s = store.create()
    token = CURRENT_SESSION_ID.set(s.id)

    sink_events: list[tuple[str, dict]] = []

    manager = BrowserManager()
    manager.event_sink = lambda key, data: sink_events.append((key, data))
    stub = StubInstance(manager.main_key())
    manager._instances[stub.key] = stub  # type: ignore[assignment]

    try:
        await manager.act("main", "info", {})
        await manager.act("main", "navigate", {"boom": True})
    except BrowserError:
        pass
    finally:
        CURRENT_SESSION_ID.reset(token)

    assert sink_events[0] == (
        "main",
        {"action": "info", "url": "https://example.com/", "title": "Example"},
    )
    assert sink_events[1][0] == "main"
    assert sink_events[1][1]["action"] == "navigate"
    assert "stub failure" in sink_events[1][1]["error"]


async def test_event_sink_publishes_session_event(tmp_path: Path) -> None:
    """The app.py sink shape: contextvar → session publish → global fan-out
    membership (browser_event is in _GLOBAL_HITL_KINDS)."""
    from nexus.server.events import SessionEvent
    from nexus.server.session_store.pubsub import _GLOBAL_HITL_KINDS

    assert "browser_event" in _GLOBAL_HITL_KINDS

    store = SessionStore(db_path=tmp_path / "sessions.sqlite")
    s = store.create()
    token = CURRENT_SESSION_ID.set(s.id)
    try:
        sid = CURRENT_SESSION_ID.get() or "*browser*"
        store.publish(sid, SessionEvent(kind="browser_event", data={"key": "main", "action": "info"}))
        assert _published(store, sid, "browser_event") == [
            {"key": "main", "action": "info"}
        ]
    finally:
        CURRENT_SESSION_ID.reset(token)


# ── manager plumbing (no Chrome) ────────────────────────────────────────────


def test_sanitize_context_keys() -> None:
    assert _sanitize("main") == "main"
    assert _sanitize("project:abc-123") == "project-abc-123"
    assert _sanitize("project:x/y z") == "project-x-y-z"
    assert _sanitize("///") == "main"


def test_profile_and_shots_layout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    manager = BrowserManager()
    assert manager.profile_dir("main").name == "chrome-profile"
    assert "browser-profiles" in str(manager.profile_dir("project:p1"))
    assert manager.shots_dir("project:p1").parent.name == "shots"


def test_screenshot_retention_prune(tmp_path: Path) -> None:
    shots = tmp_path / "shots"
    shots.mkdir()
    for i in range(30):
        (shots / f"shot-{i:04d}.png").write_bytes(b"x")
    _prune_screenshots(shots, keep=20)
    remaining = sorted(p.name for p in shots.glob("shot-*.png"))
    assert len(remaining) == 20
    # newest kept, oldest dropped
    assert remaining[0] == "shot-0010.png"
    assert remaining[-1] == "shot-0029.png"


async def test_registry_round_trip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    manager = BrowserManager()
    registry = {"project:p1": {"port": 9230, "profile_dir": "/tmp/profile-p1"}}
    manager._root.mkdir(parents=True, exist_ok=True)
    manager._registry_path.write_text(json.dumps(registry))
    assert manager._load_registry() == registry

    manager._registry = {"main": {"port": 9223, "profile_dir": "/x"}}
    manager._save_registry()
    fresh = BrowserManager()
    assert fresh._load_registry() == {"main": {"port": 9223, "profile_dir": "/x"}}


def test_status_shapes_without_chrome(tmp_path: Path, monkeypatch) -> None:
    import nexus.browser.manager as bm

    async def dead_probe(port: int, timeout: float = 1.5):
        return None

    monkeypatch.setattr(bm, "_probe", dead_probe)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    manager = BrowserManager()
    # Unknown key, nothing in the registry, nothing listening → stopped.
    async def run() -> dict:
        return await manager.status("main")

    assert asyncio.run(run()) == {"key": "main", "connected": False, "running": False}


async def test_status_main_probes_base_port_without_registry(tmp_path: Path, monkeypatch) -> None:
    """Regression: an alive Chrome on base_port with an empty registry (e.g.
    launched by the legacy vault helper) must still report running — the
    registry is a port-allocation optimization for projects, not the source
    of truth for the fixed-port main instance."""
    import nexus.browser.manager as bm

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    manager = BrowserManager()

    async def fake_probe(port: int, timeout: float = 1.5):
        return {"Browser": "Chrome/155"} if port == manager.base_port else None

    monkeypatch.setattr(bm, "_probe", fake_probe)
    status = await manager.status("main")
    assert status == {"key": "main", "connected": False, "running": True, "port": manager.base_port}

    # Project keys still require a registry entry (ports are allocated).
    assert await manager.status("project:p1") == {
        "key": "project:p1",
        "connected": False,
        "running": False,
    }

    # And list_instances reflects the discovered main instance.
    instances = await manager.list_instances()
    assert instances[0] == {
        "key": "main",
        "connected": False,
        "running": True,
        "port": manager.base_port,
    }


# ── instance action validation (no Chrome; direct BrowserError paths) ──────


async def test_instance_act_unknown_action() -> None:
    inst = BrowserInstance(
        key="main", port=9223, profile_dir=Path("/tmp/x"), shots_dir=Path("/tmp/y")
    )
    try:
        await inst.act("nope", {})
    except BrowserError as exc:
        assert "unknown action" in str(exc)
    else:
        raise AssertionError("expected BrowserError")


async def test_navigate_requires_url_and_click_requires_target() -> None:
    inst = BrowserInstance(
        key="main", port=9223, profile_dir=Path("/tmp/x"), shots_dir=Path("/tmp/y")
    )
    for args in ({"action": "navigate", "args": {}}, {"action": "click", "args": {}}):
        try:
            await inst.act(args["action"], args["args"])
        except BrowserError as exc:
            assert "requires" in str(exc)
        else:
            raise AssertionError(f"expected BrowserError for {args['action']}")


def test_find_chrome_binary_returns_path_or_none() -> None:
    from nexus.browser.manager import find_chrome_binary

    result = find_chrome_binary()
    assert result is None or Path(result).is_file()


# ── multi-bot boot reliability (regression: the port ladder) ────────────────


class FakeProc:
    def __init__(self, exited: bool = False) -> None:
        self._exited = exited

    def poll(self) -> int | None:
        return 0 if self._exited else None


def _no_launch(self) -> None:
    self._proc = FakeProc(exited=False)  # booting forever, never exits


async def test_launch_timeout_persists_port_and_caches_instance(
    tmp_path: Path, monkeypatch
) -> None:
    """The regression: a launch that outlives the in-turn wait must leave
    the port persisted + the instance cached, so retries land on the SAME
    port (old behavior allocated a new port, exec'd a second Chrome
    against the same profile → singleton handoff → permanent failure)."""
    import nexus.browser.manager as bm

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    async def dead_probe(port: int, timeout: float = 1.5):
        return None

    monkeypatch.setattr(bm, "_probe", dead_probe)
    monkeypatch.setattr(bm.BrowserInstance, "_launch", _no_launch)
    monkeypatch.setattr(bm, "LAUNCH_TIMEOUT", 0.3)
    monkeypatch.setattr(bm, "LAUNCH_POLL_INTERVAL", 0.05)
    monkeypatch.setattr(bm, "ADOPT_TIMEOUT", 0.2)
    monkeypatch.setattr(bm, "ADOPT_POLL_INTERVAL", 0.05)

    resets: list[str] = []

    async def record_reset(self, key: str, inst) -> None:
        resets.append(key)

    monkeypatch.setattr(BrowserManager, "_reset_instance", record_reset)

    alloc_calls = 0
    orig_alloc = BrowserManager._alloc_port

    async def counting_alloc(self: BrowserManager) -> int:
        nonlocal alloc_calls
        alloc_calls += 1
        return await orig_alloc(self)

    monkeypatch.setattr(BrowserManager, "_alloc_port", counting_alloc)

    manager = BrowserManager(idle_shutdown_minutes=0)
    try:
        await manager.ensure("project:p1")
        raise AssertionError("expected BrowserStillStarting")
    except BrowserStillStarting:
        pass

    # Port persisted BEFORE launch success, instance cached for retries.
    entry = manager._load_registry().get("project:p1")
    assert entry and entry["port"] == manager.base_port + 1
    assert "project:p1" in manager._instances
    port1 = manager._instances["project:p1"].port

    # Retry while adoption is in flight: no new port, no second exec.
    try:
        await manager.ensure("project:p1")
        raise AssertionError("expected BrowserStillStarting")
    except BrowserStillStarting:
        pass
    assert alloc_calls == 1
    assert manager._instances["project:p1"].port == port1
    assert entry == manager._load_registry().get("project:p1")

    # Let the adoption task finish its window: probe stays dead → it gives
    # up and resets the key (clean slate for the next attempt).
    inst = manager._instances.get("project:p1")
    if inst and inst.adopt_task:
        await asyncio.wait_for(inst.adopt_task, timeout=2)
    assert resets == ["project:p1"]


async def test_second_bot_gets_its_own_port_and_instance(tmp_path: Path, monkeypatch) -> None:
    """Two bots booted concurrently: distinct ports, distinct instances,
    both registered — the original 'second bot fails' scenario."""
    import nexus.browser.manager as bm

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    launched: set[int] = set()

    async def probe(port: int, timeout: float = 1.5):
        return {"Browser": "Chrome/155"} if port in launched else None

    monkeypatch.setattr(bm, "_probe", probe)

    def launch(self) -> None:
        launched.add(self.port)

    monkeypatch.setattr(bm.BrowserInstance, "_launch", launch)

    async def connect_ok(self) -> None:
        return None

    monkeypatch.setattr(bm.BrowserInstance, "_connect_with_retry", connect_ok)

    manager = BrowserManager(idle_shutdown_minutes=0)
    a = await manager.ensure("project:a")
    b = await manager.ensure("project:b")
    assert a.port != b.port
    assert manager._instances["project:a"] is a
    assert manager._instances["project:b"] is b
    registry = manager._load_registry()
    assert registry["project:a"]["port"] == a.port
    assert registry["project:b"]["port"] == b.port


async def test_singleton_handoff_fails_fast(tmp_path: Path, monkeypatch) -> None:
    """A child that exits immediately (profile absorbed by an existing
    Chrome) must fail fast with a clear error, not after the full wait."""
    import nexus.browser.manager as bm

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(bm, "LAUNCH_POLL_INTERVAL", 0.05)

    async def dead_probe(port: int, timeout: float = 1.5):
        return None

    monkeypatch.setattr(bm, "_probe", dead_probe)

    def launch(self) -> None:
        self._proc = FakeProc(exited=True)  # singleton handoff: instant exit

    monkeypatch.setattr(bm.BrowserInstance, "_launch", launch)

    inst = BrowserInstance(
        key="project:p1", port=9230,
        profile_dir=tmp_path / "p1", shots_dir=tmp_path / "s1",
    )
    try:
        await inst.ensure()
        raise AssertionError("expected BrowserError")
    except BrowserStillStarting:
        raise AssertionError("handoff must fail fast, not report still-starting")
    except BrowserError as exc:
        assert "already in use" in str(exc)


def test_alloc_port_skips_registry_ports(tmp_path: Path, monkeypatch) -> None:
    """Alloc must not hand key B a port recorded for key A (A's next probe
    would find B's Chrome and drive it)."""
    import nexus.browser.manager as bm

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    async def dead_probe(port: int, timeout: float = 1.5):
        return None

    monkeypatch.setattr(bm, "_probe", dead_probe)
    manager = BrowserManager()
    manager._root.mkdir(parents=True, exist_ok=True)
    manager._registry_path.write_text(json.dumps({
        "project:a": {"port": manager.base_port + 5, "profile_dir": "/tmp/a"},
    }))
    port = asyncio.run(manager._alloc_port())
    assert port == manager.base_port + 1  # skipped over 9228 (base+5)


# ── CDP event fan-out + live stream ─────────────────────────────────────────


async def test_event_dispatch_reaches_subscriber() -> None:
    inst = BrowserInstance(
        key="main", port=9223, profile_dir=Path("/tmp/x"), shots_dir=Path("/tmp/y")
    )
    q = inst.subscribe(["Page.screencastFrame"])
    inst._dispatch_event("Page.screencastFrame", {
        "method": "Page.screencastFrame",
        "params": {"data": "abc", "sessionId": 1},
    })
    inst._dispatch_event("Page.unrelated", {"method": "Page.unrelated"})
    assert q.get_nowait()["params"]["data"] == "abc"
    assert q.qsize() == 0  # unsubscribed methods never enqueued
    inst.unsubscribe(q)
    inst._dispatch_event("Page.screencastFrame", {
        "method": "Page.screencastFrame", "params": {"data": "late", "sessionId": 1}
    })
    assert q.qsize() == 0
    assert inst.screencast_subscribers() == 0


async def test_stream_yields_frames_and_acks(tmp_path: Path, monkeypatch) -> None:
    """manager.stream: buffered screencast frames come out as SSE dicts,
    each acked; the cast stops when the last subscriber leaves."""
    import nexus.browser.manager as bm

    monkeypatch.setattr(bm, "STREAM_MIN_FRAME_INTERVAL", 0.0)
    monkeypatch.setattr(bm, "STREAM_HEARTBEAT_SECONDS", 5.0)

    inst = BrowserInstance(
        key="main", port=9223,
        profile_dir=tmp_path / "p", shots_dir=tmp_path / "s",
    )
    sent: list[tuple[str, dict | None]] = []

    async def fake_send(method: str, params: dict | None = None, **kw) -> dict:
        sent.append((method, params))
        return {}

    inst._send = fake_send  # type: ignore[method-assign]

    manager = BrowserManager()

    async def ensure_inst(key: str) -> BrowserInstance:
        return inst

    manager.ensure = ensure_inst  # type: ignore[method-assign]

    async def dispatcher() -> None:
        # Wait for the stream to subscribe, then push three frames.
        await asyncio.sleep(0.05)
        for i in range(3):
            inst._dispatch_event("Page.screencastFrame", {
                "method": "Page.screencastFrame",
                "params": {"data": f"frm{i}", "sessionId": "s1"},
            })

    task = asyncio.create_task(dispatcher())

    out = []
    gen = manager.stream("main")
    async for ev in gen:
        out.append(ev)
        if len([e for e in out if e["type"] == "frame"]) == 3:
            break
    await gen.aclose()
    await task

    frames = [e for e in out if e["type"] == "frame"]
    assert [f["data"] for f in frames] == ["frm0", "frm1", "frm2"]
    acks = [p for m, p in sent if m == "Page.screencastFrameAck"]
    assert acks == [{"sessionId": "s1"}] * 3
    # The cast started with the stream and stopped with it (no subscribers
    # left; with no open ws the stop is flag-only — the re-arm covers the
    # next connection).
    assert sent[0][0] == "Page.startScreencast"
    assert inst._screencast_on is False
    assert inst.screencast_subscribers() == 0


# ── new perception actions (NBIE) ────────────────────────────────────────


async def test_actions_include_probe_actions() -> None:
    from nexus.browser.manager import _VERIFY_ACTIONS, ACTIONS, CONTROL_ACTIONS

    for action in ("find_element", "inspect_element", "viewport_info", "page_signature", "interactive_map"):
        assert action in ACTIONS, action
    # Perception actions are read-only -> never remote-controlled.
    assert {"find_element", "inspect_element", "viewport_info", "page_signature", "interactive_map"}.isdisjoint(
        CONTROL_ACTIONS
    )
    # Mutating actions get pre/post pageSignature diffs.
    assert {"click", "tap", "type", "key", "scroll"} <= _VERIFY_ACTIONS


async def _make_inst(monkeypatch, responses) -> BrowserInstance:
    from nexus.browser.manager import BrowserInstance

    inst = BrowserInstance(
        key="main", port=9223, profile_dir=Path("/tmp/p"), shots_dir=Path("/tmp/s")
    )

    async def fake_evaluate(expression, *, await_promise=False, timeout=30.0):
        for needle, value in responses:
            if f'self.__nexus_probes("{needle}",' in expression:
                return value
        return {}

    inst.evaluate = fake_evaluate  # type: ignore[method-assign]
    return inst


async def test_act_find_element_inspect_viewport_signature_map(tmp_path, monkeypatch) -> None:
    responses = [
        ("findElement", {"ok": True, "count": 1, "elements": [{"selector": "#sb", "role": "button", "name": "Search", "text": "Search", "box": {"x": 1, "y": 2, "w": 3, "h": 4}, "enabled": True}]}),
        ("inspectElement", {"ok": True, "selector": "#pw", "tag": "input", "attrs": {"type": "password", "value": "\u2022\u2022\u2022\u2022\u2022"}}),
        ("viewportInfo", {"ok": True, "width": 1440, "height": 900, "scrollX": 0, "scrollY": 0, "devicePixelRatio": 1}),
        ("pageSignature", {"ok": True, "url": "https://example.com/", "title": "Ex", "textHash": "abc", "dialogs": []}),
        ("interactiveMap", {"ok": True, "count": 0, "nodes": []}),
    ]
    inst = await _make_inst(monkeypatch, responses)

    r = await inst.act("find_element", {"role": "button", "limit": 5})
    assert r["ok"] is True and r["count"] == 1 and r["elements"][0]["selector"] == "#sb"

    r = await inst.act("inspect_element", {"selector": "#pw"})
    assert r["ok"] is True
    # Password value redacted inside the probe.
    assert r["attrs"]["value"] == "\u2022\u2022\u2022\u2022\u2022"

    r = await inst.act("viewport_info", {})
    assert r["width"] == 1440 and r["height"] == 900

    r = await inst.act("page_signature", {})
    assert r["textHash"] == "abc" and r["dialogs"] == []

    r = await inst.act("interactive_map", {})
    assert r["count"] == 0


async def test_act_verify_click_diffs_page_signature(tmp_path, monkeypatch) -> None:
    import json

    from nexus.browser.manager import BrowserInstance

    sigs = [
        {"url": "https://a.com/", "title": "A", "textHash": "hash1", "dialogs": []},
        {"url": "https://b.com/", "title": "B", "textHash": "hash2", "dialogs": [{"role": "dialog", "name": "alert"}]},
    ]
    idx = 0

    async def fake_evaluate(expression, *, await_promise=False, timeout=30.0):
        nonlocal idx
        if "pageSignature" in expression:
            # Settle-poll re-reads until stable — clamp to the last
            # signature: the page "lands" on B and stays there.
            out = sigs[min(idx, len(sigs) - 1)]
            idx += 1
            return out
        if "getBoundingClientRect" in expression:
            return {"x": 100, "y": 200}
        return json.dumps({"url": "https://b.com/", "title": "B"})

    inst = BrowserInstance(
        key="main", port=9223, profile_dir=Path("/tmp/p"), shots_dir=Path("/tmp/s")
    )
    inst.evaluate = fake_evaluate  # type: ignore[method-assign]

    r = await inst.act("click", {"selector": "#x"})
    assert r["url_changed"] is True
    assert r["state_changed"] is True
    assert r["dialogs"] == [{"role": "dialog", "name": "alert"}]
    assert r["page_signature"]["textHash"] == "hash2"


async def test_act_read_uses_readpage_probe(tmp_path, monkeypatch) -> None:
    from nexus.browser.manager import BrowserInstance

    seen = {}

    async def fake_evaluate(expression, *, await_promise=False, timeout=30.0):
        seen["expr"] = expression
        return {"ok": True, "text": "hello", "contentHash": "h1", "complete": True}

    inst = BrowserInstance(
        key="main", port=9223, profile_dir=Path("/tmp/p"), shots_dir=Path("/tmp/s")
    )
    inst.evaluate = fake_evaluate  # type: ignore[method-assign]

    r = await inst.act("read", {})
    assert r["text"] == "hello" and r["complete"] is True
    assert "readPage" in seen["expr"]
    assert '"maxChars": 8000' in seen["expr"]


# ── Phase C: CDP-native perception ──────────────────────────────────────


async def _make_inst_phase_c(monkeypatch, sends, evaluates) -> BrowserInstance:
    from nexus.browser.manager import BrowserInstance

    inst = BrowserInstance(
        key="main", port=9223, profile_dir=Path("/tmp/p"), shots_dir=Path("/tmp/s")
    )

    send_table = {method: resp for method, resp in sends}

    async def fake_send(method, params=None, *, timeout=30.0):
        return send_table.get(method, {})

    inst._send = fake_send  # type: ignore[method-assign]

    async def fake_eval(expression, *, await_promise=False, timeout=30.0):
        for needle, value in evaluates:
            if needle in expression:
                return value
        return None

    inst.evaluate = fake_eval  # type: ignore[method-assign]
    return inst


async def test_act_ax_tree_redacts_passwords(tmp_path, monkeypatch) -> None:
    sends = [
        ("Accessibility.enable", {}),
        (
            "Accessibility.getFullAXTree",
            {
                "rootId": "r1",
                "nodes": [
                    {
                        "role": {"value": "text field"},
                        "name": "senha",
                        "value": "secret123",
                        "backendDOMNodeId": 5,
                        "properties": [{"name": "required", "value": {"type": "boolean", "value": True}}],
                    },
                    {"role": {"value": "heading"}, "name": "Login", "value": "", "backendDOMNodeId": 9},
                ],
            },
        ),
        ("DOM.querySelectorAll", {"nodeIds": [1]}),
        ("DOM.describeNode", {"node": {"backendNodeId": 5}}),
    ]
    inst = await _make_inst_phase_c(monkeypatch, sends, [])

    # json format (opt-in) keeps the structured nodes
    r = await inst.act("ax_tree", {"format": "json"})
    assert r["ok"] is True and r["rootId"] == "r1"
    nodes = r["nodes"]
    pw = next(n for n in nodes if n["name"] == "senha")
    assert pw["value"] == "•••••", pw  # password redacted
    assert pw["state"] == {"required": True}  # props surfaced compactly
    heading = next(n for n in nodes if n["name"] == "Login")
    assert heading["value"] == ""  # non-password untouched
    assert heading["state"] == {}


async def test_act_element_screenshot(tmp_path, monkeypatch) -> None:
    import base64

    png = base64.b64encode(b"PNG-FRAME-BYTES").decode()
    sends = [("Page.captureScreenshot", {"data": png})]
    evaluates = [
        ("getBoundingClientRect", {"x": 10, "y": 20, "w": 100, "h": 40}),
        ("JSON.stringify({url: location.href", '{"url": "https://example.com/", "title": "Ex"}'),
    ]
    inst = await _make_inst_phase_c(monkeypatch, sends, evaluates)

    r = await inst.act("element_screenshot", {"selector": "#btn", "padding": 8})
    assert r["ok"] is True
    assert r["selector"] == "#btn"
    assert r["box"] == {"x": 10, "y": 20, "w": 100, "h": 40}
    assert r["clip"] == {"x": 2, "y": 12, "width": 116, "height": 56, "scale": 1}
    shot = Path(r["screenshot"])
    assert shot.exists() and shot.read_bytes() == b"PNG-FRAME-BYTES"
    assert r["url"]  # info merged in


# ── Phase D: multi-target CDP ───────────────────────────────────────────


async def test_act_list_tabs(tmp_path, monkeypatch) -> None:
    from nexus.browser.manager import BrowserInstance

    inst = BrowserInstance(
        key="main", port=9223, profile_dir=Path("/tmp/p"), shots_dir=Path("/tmp/s")
    )

    async def fake_list_targets():
        return [
            {
                "targetId": "t1",
                "url": "https://example.com/",
                "title": "Example",
                "type": "page",
                "webSocketDebuggerUrl": "ws://x",
            },
            {
                "targetId": "t2",
                "url": "https://other.com/",
                "title": "Other",
                "type": "page",
                "webSocketDebuggerUrl": "ws://y",
            },
        ]

    inst._list_targets = fake_list_targets  # type: ignore[method-assign]

    r = await inst.act("list_tabs", {})
    assert r["ok"] is True and r["count"] == 2
    assert [t["targetId"] for t in r["tabs"]] == ["t1", "t2"]
    assert r["tabs"][0]["url"] == "https://example.com/"


async def test_act_switch_tab_missing_targetid(tmp_path, monkeypatch) -> None:
    from nexus.browser.manager import BrowserError, BrowserInstance

    inst = BrowserInstance(
        key="main", port=9223, profile_dir=Path("/tmp/p"), shots_dir=Path("/tmp/s")
    )
    try:
        await inst.act("switch_tab", {})
    except BrowserError as exc:
        assert "targetId" in str(exc)
    else:
        raise AssertionError("expected BrowserError for missing targetId")


async def test_act_switch_tab_unknown_target(tmp_path, monkeypatch) -> None:
    from nexus.browser.manager import BrowserError, BrowserInstance

    inst = BrowserInstance(
        key="main", port=9223, profile_dir=Path("/tmp/p"), shots_dir=Path("/tmp/s")
    )

    async def fake_list_targets():
        return [
            {
                "targetId": "t1",
                "url": "https://example.com/",
                "title": "Example",
                "type": "page",
                "webSocketDebuggerUrl": "ws://x",
            }
        ]

    inst._list_targets = fake_list_targets  # type: ignore[method-assign]
    try:
        await inst.act("switch_tab", {"targetId": "nope"})
    except BrowserError as exc:
        assert "no such tab" in str(exc)
    else:
        raise AssertionError("expected BrowserError for unknown target")


# ── Phase E: NBIE metrics (cache-hit + per-level counts) ────────────────


async def test_act_read_cache_hit_metric(tmp_path, monkeypatch) -> None:
    from nexus.browser.manager import BrowserInstance

    async def fake_evaluate(expression, *, await_promise=False, timeout=30.0):
        return {"ok": True, "text": "hello", "contentHash": "h1", "complete": True}

    inst = BrowserInstance(
        key="main", port=9223, profile_dir=Path("/tmp/p"), shots_dir=Path("/tmp/s")
    )
    inst.evaluate = fake_evaluate  # type: ignore[method-assign]

    r1 = await inst.act("read", {})
    assert r1["cache_hit"] is False  # first read is never a cache-hit
    r2 = await inst.act("read", {})
    assert r2["cache_hit"] is True  # same contentHash → agent re-paid for nothing
    assert inst.level_counts == {"text": 2}


async def test_manager_event_carries_nbie_metrics(tmp_path, monkeypatch) -> None:
    sink_events: list[tuple[str, dict]] = []

    class LevelStubInstance(StubInstance):
        async def act(self, action: str, args: dict) -> dict:
            self.calls.append((action, dict(args)))
            self.level_counts[action] = self.level_counts.get(action, 0) + 1
            result = {"url": "https://example.com/", "title": "Example"}
            if action == "read":
                result["cache_hit"] = False
            return result

    manager = StubManager()
    stub = LevelStubInstance("main")
    manager._stub = stub
    manager.event_sink = lambda key, data: sink_events.append((key, data))

    async def fake_ensure(key: str) -> BrowserInstance:
        return stub

    manager.ensure = fake_ensure  # type: ignore[method-assign]

    await manager.act("main", "read", {})
    ev = sink_events[-1][1]
    assert ev["nbie"]["level"] == "text"
    assert ev["nbie"]["cache_hit"] is False
    assert ev["nbie"]["levels"] == {"read": 1}

    await manager.act("main", "screenshot", {})
    ev = sink_events[-1][1]
    assert ev["nbie"]["level"] == "vision"
    assert ev["nbie"]["levels"] == {"read": 1, "screenshot": 1}


async def test_nbie_ring_buffer_records_success_and_error() -> None:
    from nexus.browser.manager import BrowserError

    manager = StubManager()

    class BoomInstance(StubInstance):
        async def act(self, action: str, args: dict) -> dict:
            if action == "read":
                return {"url": "https://x/", "contentHash": "h1", "cache_hit": False}
            raise BrowserError("kaboom")

    stub = BoomInstance("main")

    async def fake_ensure(key: str) -> StubInstance:
        return stub

    manager.ensure = fake_ensure  # type: ignore[method-assign]

    await manager.act("main", "read", {})
    try:
        await manager.act("main", "js", {"expression": "1"})
    except BrowserError:
        pass
    assert len(manager.nbie_events) == 2
    ok_ev = manager.nbie_events[0]
    assert ok_ev["action"] == "read" and ok_ev["ok"] is True and ok_ev["level"] == "text"
    assert ok_ev["cache_hit"] is False
    err_ev = manager.nbie_events[1]
    assert err_ev["action"] == "js" and err_ev["ok"] is False
    assert "kaboom" in (err_ev.get("error") or "")


def test_aggregate_nbie_events() -> None:
    import time as _time

    from nexus.browser.manager import aggregate_nbie_events

    now = _time.time()
    events = [
        {"ts": now - 90, "key": "main", "action": "navigate", "level": "nav", "ok": True, "cache_hit": None},
        {"ts": now - 80, "key": "main", "action": "read", "level": "text", "ok": True, "cache_hit": False},
        {"ts": now - 70, "key": "main", "action": "read", "level": "text", "ok": True, "cache_hit": True},
        {"ts": now - 60, "key": "main", "action": "screenshot", "level": "vision", "ok": True, "cache_hit": None},
        {"ts": now - 10, "key": "p1", "action": "click", "level": "action", "ok": False, "cache_hit": None, "error": "sel not found"},
    ]
    r = aggregate_nbie_events(events)
    assert r["totals"]["actions"] == 5
    assert r["totals"]["reads"] == 2
    assert r["totals"]["cache_hits"] == 1
    assert r["totals"]["cache_hit_rate"] == 0.5
    assert r["totals"]["errors"] == 1
    assert r["levels"] == {"nav": 1, "text": 2, "vision": 1, "action": 1}
    assert r["keys"] == {"main": 4, "p1": 1}
    assert r["recent_errors"] and r["recent_errors"][0]["action"] == "click"

    # trailing-window filter: only the last 30s (the click error)
    r30 = aggregate_nbie_events(events, minutes=0.5)
    assert r30["totals"]["actions"] == 1
    assert r30["totals"]["errors"] == 1

    # cache-hit rate ignores failed reads
    r_err_read = aggregate_nbie_events(
        [{"ts": now, "key": "k", "action": "read", "level": "text", "ok": False, "cache_hit": None, "error": "x"}]
    )
    assert r_err_read["totals"]["reads"] == 0
    assert r_err_read["totals"]["cache_hit_rate"] is None


def test_aggregate_nbie_by_surface() -> None:
    import time as _time

    from nexus.browser.manager import aggregate_nbie_events

    now = _time.time()
    events = [
        {"ts": now - 5, "key": "main", "action": "read", "level": "text", "ok": True, "cache_hit": False, "surface": "cdp"},
        {"ts": now - 4, "key": "ext:abc12345", "action": "read", "level": "text", "ok": True, "cache_hit": True, "surface": "ext"},
        {"ts": now - 3, "key": "ext:abc12345", "action": "find_element", "level": "element", "ok": True, "cache_hit": None, "surface": "ext"},
        {"ts": now - 2, "key": "ext:abc12345", "action": "page_signature", "level": "element", "ok": False, "cache_hit": None, "error": "no access", "surface": "ext"},
    ]
    r = aggregate_nbie_events(events)
    assert r["by_surface"]["cdp"] == {"actions": 1, "reads": 1, "cache_hits": 0, "errors": 0}
    assert r["by_surface"]["ext"] == {"actions": 3, "reads": 1, "cache_hits": 1, "errors": 1}
    # legacy events without a surface key default to cdp
    r2 = aggregate_nbie_events([{"ts": now, "key": "main", "action": "navigate", "level": "nav", "ok": True, "cache_hit": None}])
    assert "cdp" in r2["by_surface"]


# ── discovery cache + snapshot refs + extract ────────────────────────────


async def test_find_element_discovery_cache_and_refs(tmp_path, monkeypatch) -> None:
    from nexus.browser.manager import BrowserInstance

    calls: list[str] = []
    page_hash = "H1"

    async def fake_evaluate(expression, *, await_promise=False, timeout=30.0):
        calls.append(expression[:160])
        # The probe expressions embed the WHOLE library source, so bare-name
        # checks ("pageSignature" in expr) match every expression — key on
        # the dispatcher call instead.
        if '("findElement",' in expression:
            return {
                "ok": True,
                "elements": [{"selector": "#go", "tag": "button", "text": "Go", "box": {"x": 1, "y": 2, "w": 10, "h": 10}}],
                "count": 1,
            }
        if '("pageSignature",' in expression:
            return {"ok": True, "url": "https://x/", "title": "X", "textHash": page_hash, "dialogs": []}
        if "getBoundingClientRect" in expression:  # _CENTER_JS (standalone)
            return {"x": 6.0, "y": 7.0}
        return {}

    inst = BrowserInstance(
        key="main", port=9223, profile_dir=Path("/tmp/p"), shots_dir=Path("/tmp/s")
    )
    inst.evaluate = fake_evaluate  # type: ignore[method-assign]
    inst._send = AsyncMock(return_value={})  # type: ignore[method-assign]

    r1 = await inst.act("find_element", {"role": "button"})
    assert r1["ok"] is True and r1["count"] == 1
    assert r1["snapshotHash"] == "H1"
    assert r1["elements"][0]["ref"] == 1

    # identical query on an unchanged page → compact cache marker
    r2 = await inst.act("find_element", {"role": "button"})
    assert r2.get("cached") is True and r2["count"] == 1
    assert "elements" not in r2  # the list is not re-sent

    # different filters → fresh result
    r3 = await inst.act("find_element", {"role": "link"})
    assert r3.get("cached") is not True and r3["elements"][0]["ref"] == 1

    # click by ref resolves through the snapshot (selector-first)
    click = await inst.act("click", {"ref": 1})
    assert click.get("url_changed") is False  # executed + verified (hash held)
    assert any("getBoundingClientRect" in c for c in calls)  # center of #go

    # page changed → refs invalidated
    page_hash = "H2"
    from nexus.browser.manager import BrowserError as _BE

    try:
        await inst.act("click", {"ref": 1})
    except _BE as exc:
        assert "page changed" in str(exc)
    else:
        raise AssertionError("expected stale-ref error")


async def test_ax_tree_compact_default(tmp_path, monkeypatch) -> None:
    sends = [
        ("Accessibility.enable", {}),
        (
            "Accessibility.getFullAXTree",
            {
                "rootId": "r1",
                "nodes": [
                    {
                        "role": {"value": "text field"},
                        "name": {"value": "senha"},
                        "value": "secret123",
                        "backendDOMNodeId": 5,
                        "properties": [{"name": "required", "value": {"type": "boolean", "value": True}}],
                    },
                ],
            },
        ),
        ("DOM.querySelectorAll", {"nodeIds": [1]}),
        ("DOM.describeNode", {"node": {"backendNodeId": 5}}),
    ]
    inst = await _make_inst_phase_c(monkeypatch, sends, [])
    r = await inst.act("ax_tree", {})
    assert r["format"] == "compact"
    assert r["count"] == 1
    line = r["lines"][0]
    assert '- text field "senha"' in line
    assert "[required]" in line
    assert "secret123" not in line  # redaction holds in compact form
    assert "•••••" in line and "[ref=1]" in line


async def test_browser_tool_extract_subllm(tmp_path, monkeypatch) -> None:
    from nexus.browser import extract as page_extract
    from nexus.browser.tool import BrowserHandler

    class ReadManager(StubManager):
        async def act(self, key: str, action: str, args: dict) -> dict:
            if action == "read":
                return {"ok": True, "url": "https://x/", "title": "X", "text": "The price is 42 euros", "contentHash": "h1"}
            raise AssertionError(f"unexpected action {action}")

    async def fake_llm(prompt: str) -> str:
        assert "The price is 42 euros" in prompt
        return "42 euros"

    page_extract.set_extract_llm(fake_llm)
    try:
        handler = BrowserHandler(manager=ReadManager(), session_store=None)
        out = json.loads(await handler.invoke({"action": "extract", "question": "what is the price?"}))
        assert out["ok"] is True
        assert out["answer"] == "42 euros"
        assert "text" not in out  # the page text never enters the result
        assert out["contentHash"] == "h1"

        out = json.loads(await handler.invoke({"action": "extract"}))
        assert out["ok"] is False and "question" in out["error"]
    finally:
        page_extract.set_extract_llm(None)




async def test_browser_save_flow_and_replay(tmp_path, monkeypatch) -> None:
    """CDP surface: successful manager.act calls are recorded per context;
    save_flow persists them; replay re-executes via the manager."""
    from nexus.browser import flows as flow_store
    from nexus.browser.tool import BrowserHandler

    fd = tmp_path / "flows"
    fd.mkdir()
    monkeypatch.setattr(flow_store, "flows_dir", lambda: fd)

    class RecManager(StubManager):
        def __init__(self) -> None:
            super().__init__()
            self.acts: list[tuple[str, str]] = []

        async def act(self, key: str, action: str, args: dict) -> dict:
            self.acts.append((key, action))
            if action == "click" and args.get("selector") == "#gone":
                raise BrowserError(f"element not found: #gone")
            return {"url": "https://x/", "title": "X"}

    mgr = RecManager()
    handler = BrowserHandler(manager=mgr, session_store=None)

    out = json.loads(await handler.invoke({"action": "navigate", "url": "https://x/"}))
    assert out["ok"] is True
    await handler.invoke({"action": "read"})
    await handler.invoke({"action": "click", "selector": "#go"})
    # failed act must NOT be recorded
    try:
        await handler.invoke({"action": "click", "selector": "#gone"})
    except BrowserError:
        pass

    saved = json.loads(await handler.invoke({"action": "save_flow", "name": "re-add"}))
    assert saved["ok"] is True
    assert saved["steps_state_changing"] == 2  # navigate + click (read dropped)

    # replay on the manager: navigate + click again, then abort on #gone
    flow = flow_store.load_flow("re-add")
    assert [s["action"] for s in flow["steps"]] == ["navigate", "click"]
    mgr.acts.clear()
    # replay with a flow whose second step fails:
    flow_store.save_flow(
        "boom",
        "cdp",
        [
            {"action": "navigate", "args": {"url": "https://x/"}},
            {"action": "click", "args": {"selector": "#gone"}},
        ],
    )
    r = json.loads(await handler.invoke({"action": "replay", "name": "boom"}))
    assert r["ok"] is False and r["executed"] == 1 and r["total"] == 2
    assert r["steps"][1]["ok"] is False and "#gone" in (r["steps"][1]["error"] or "")
    listing = json.loads(await handler.invoke({"action": "flows"}))
    assert any(f["name"] == "boom" for f in listing["flows"])
