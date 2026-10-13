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
