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
    assert props["action"]["enum"] == list(ACTIONS)
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
    from nexus.server.session_store.pubsub import _GLOBAL_HITL_KINDS
    from nexus.server.events import SessionEvent

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


def _no_launch(self) -> None:  # noqa: ANN001 — stub
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

    async def record_reset(self, key: str, inst) -> None:  # noqa: ANN001 — stub
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

    def launch(self) -> None:  # noqa: ANN001 — stub
        launched.add(self.port)

    monkeypatch.setattr(bm.BrowserInstance, "_launch", launch)

    async def connect_ok(self) -> None:  # noqa: ANN001 — stub
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

    def launch(self) -> None:  # noqa: ANN001 — stub
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
    from nexus.browser.manager import ACTIONS, CONTROL_ACTIONS, _VERIFY_ACTIONS

    for action in ("find_element", "inspect_element", "viewport_info", "page_signature", "interactive_map"):
        assert action in ACTIONS, action
    # Perception actions are read-only -> never remote-controlled.
    assert {"find_element", "inspect_element", "viewport_info", "page_signature", "interactive_map"}.isdisjoint(
        CONTROL_ACTIONS
    )
    # Mutating actions get pre/post pageSignature diffs.
    assert {"click", "tap", "type", "key", "scroll"} <= _VERIFY_ACTIONS


async def _make_inst(monkeypatch, responses) -> "BrowserInstance":
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
            out = sigs[idx]
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


async def _make_inst_phase_c(monkeypatch, sends, evaluates) -> "BrowserInstance":
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

    r = await inst.act("ax_tree", {})
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
    from nexus.browser.manager import BrowserInstance, BrowserError

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
    from nexus.browser.manager import BrowserInstance, BrowserError

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

