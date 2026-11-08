"""Remote browser control: action gating + tap/key action semantics.

The route-level gate is exercised through TestClient with a fake manager
(no Chrome needed): loopback may always control, non-loopback only with
``[browser].remote_control = true``, read-only actions stay open. Tap/key
semantics (normalized coords → CDP events, key descriptors) are unit-tested
against a stub BrowserInstance.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from nexus.browser.manager import ACTIONS, CONTROL_ACTIONS, _SPECIAL_KEYS


# ── action registry invariants ───────────────────────────────────────────────


def test_control_actions_subset_of_actions() -> None:
    assert CONTROL_ACTIONS <= set(ACTIONS)
    # The remote-control surface the iOS app relies on is all there.
    assert {"tap", "key", "type", "scroll", "navigate"} <= CONTROL_ACTIONS
    # Watching is not driving — reads stay open.
    assert not ({"read", "info", "screenshot", "dom_text", "wait_for"} & CONTROL_ACTIONS)


def test_special_keys_cover_the_mobile_keyboard() -> None:
    for name in ("enter", "backspace", "tab", "arrowup", "arrowdown", "arrowleft", "arrowright"):
        desc = _SPECIAL_KEYS[name]
        assert desc["vk"] > 0 and desc["key"] and desc["code"]


# ── tap: normalized coordinates → CDP mouse events ───────────────────────────


class _StubInstance:
    """Minimal BrowserInstance double: records CDP sends, fake viewport."""

    def __init__(self, viewport: dict[str, float]) -> None:
        self.viewport_size = viewport
        self.sends: list[tuple[str, dict[str, Any]]] = []
        self.last_active = 0.0
        self.last_page: dict[str, Any] = {}

    async def viewport(self) -> dict[str, float]:
        return self.viewport_size

    async def evaluate(self, _expr: str, **_kw: Any) -> dict[str, Any]:
        return {"url": "https://example.com", "title": "Example"}

    async def _act_info(self, _args: dict[str, Any]) -> dict[str, Any]:
        return {"url": "https://example.com", "title": "Example"}

    async def _send(self, method: str, params: dict[str, Any]) -> None:
        self.sends.append((method, params))


@pytest.mark.asyncio
async def test_act_tap_scales_normalized_to_viewport() -> None:
    from nexus.browser.manager import BrowserInstance  # noqa: F401 — for reference

    inst = _StubInstance({"width": 1440.0, "height": 900.0})
    # Call the unbound method from the real class against the stub.
    import nexus.browser.manager as m

    await m.BrowserInstance._act_tap(inst, {"x_norm": 0.5, "y_norm": 0.25})  # type: ignore[arg-type]
    presses = [p for meth, p in inst.sends if meth == "Input.dispatchMouseEvent"]
    assert len(presses) == 2  # pressed + released
    assert presses[0]["x"] == pytest.approx(720.0)
    assert presses[0]["y"] == pytest.approx(225.0)


@pytest.mark.asyncio
async def test_act_tap_rejects_out_of_range() -> None:
    import nexus.browser.manager as m

    inst = _StubInstance({"width": 1440.0, "height": 900.0})
    from nexus.browser.manager import BrowserError

    with pytest.raises(BrowserError):
        await m.BrowserInstance._act_tap(inst, {"x_norm": 1.5, "y_norm": 0.5})  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_act_key_dispatches_enter_with_text() -> None:
    import nexus.browser.manager as m

    inst = _StubInstance({"width": 1440.0, "height": 900.0})
    await m.BrowserInstance._act_key(inst, {"key": "enter"})  # type: ignore[arg-type]
    keys = [p for meth, p in inst.sends if meth == "Input.dispatchKeyEvent"]
    assert len(keys) == 2
    # Enter must carry \r as text on keyDown — without it, submits don't fire.
    assert keys[0]["type"] == "keyDown"
    assert keys[0]["text"] == "\r"
    assert keys[0]["windowsVirtualKeyCode"] == 13
    assert keys[1]["type"] == "keyUp"


@pytest.mark.asyncio
async def test_act_key_rejects_unknown() -> None:
    import nexus.browser.manager as m

    from nexus.browser.manager import BrowserError

    inst = _StubInstance({"width": 1440.0, "height": 900.0})
    with pytest.raises(BrowserError, match="unsupported key"):
        await m.BrowserInstance._act_key(inst, {"key": "q"})  # type: ignore[arg-type]


# ── route-level remote_control gate ──────────────────────────────────────────


class _FakeManager:
    async def act(self, key: str, action: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"url": "https://example.com", "title": "ok", "action": action}

    async def stop(self, key: str) -> dict[str, Any]:
        return {"stopped": key}


@pytest.fixture()
def _fake_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexus.server.routes import browser as browser_route

    monkeypatch.setattr(browser_route, "_manager", lambda request: _FakeManager())


def _set_remote_control(monkeypatch: pytest.MonkeyPatch, value: bool) -> None:
    from nexus.main import app

    cfg = app.state.mutable_state["cfg"]
    monkeypatch.setattr(cfg.browser, "remote_control", value)


def _loopback() -> TestClient:
    from nexus.main import app

    return TestClient(app, client=("127.0.0.1", 12345))


def _tailnet(token: str) -> TestClient:
    from nexus.main import app

    return TestClient(app, client=("100.64.10.99", 54321), headers={"Authorization": f"Bearer {token}"})


@pytest.fixture(autouse=True)
def _pairing_token(tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
    import nexus.pairing as pairing

    monkeypatch.setattr(pairing, "HOME_DIR", tmp_path / ".nexus")
    monkeypatch.setattr(pairing, "TOKEN_PATH", tmp_path / ".nexus" / "client_token")
    return pairing.get_or_create_token()


def test_loopback_may_always_control(_fake_browser) -> None:
    r = _loopback().post("/browser/action", json={"action": "click", "x": 10, "y": 10})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_remote_control_blocked_by_default(_fake_browser, monkeypatch, _pairing_token) -> None:
    _set_remote_control(monkeypatch, False)
    r = _tailnet(_pairing_token).post("/browser/action", json={"action": "tap", "x_norm": 0.5, "y_norm": 0.5})
    assert r.status_code == 403
    assert "remote_control" in r.json()["detail"]


def test_remote_control_allowed_when_opted_in(_fake_browser, monkeypatch, _pairing_token) -> None:
    _set_remote_control(monkeypatch, True)
    client = _tailnet(_pairing_token)
    r = client.post("/browser/action", json={"action": "tap", "x_norm": 0.5, "y_norm": 0.5})
    assert r.status_code == 200
    r2 = client.post("/browser/action", json={"action": "key", "key_name": "enter"})
    assert r2.status_code == 200
    r3 = client.post("/browser/action", json={"action": "scroll", "delta_y": -300})
    assert r3.status_code == 200


def test_remote_watch_actions_stay_open(_fake_browser, monkeypatch, _pairing_token) -> None:
    _set_remote_control(monkeypatch, False)
    client = _tailnet(_pairing_token)
    r = client.post("/browser/action", json={"action": "info"})
    assert r.status_code == 200
    r2 = client.post("/browser/action", json={"action": "read"})
    assert r2.status_code == 200


def test_remote_stop_is_control_gated(_fake_browser, monkeypatch, _pairing_token) -> None:
    _set_remote_control(monkeypatch, False)
    r = _tailnet(_pairing_token).post("/browser/stop", json={})
    assert r.status_code == 403
    _set_remote_control(monkeypatch, True)
    r2 = _tailnet(_pairing_token).post("/browser/stop", json={})
    assert r2.status_code == 200
