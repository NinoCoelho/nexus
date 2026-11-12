"""``GET /status`` TTS availability flag tests.

The iOS app used to detect TTS by probing ``POST /tts/synthesize`` and
hiding the Speak option on 404/501 — a wasted round-trip on every connect.
``/status`` now carries ``features.tts``: True only when the pipeline is
operational (enabled in settings AND the Piper engine importable), so
clients can trust it as a capability flag. The change is additive — every
pre-existing ``/status`` field keeps its shape.
"""

from __future__ import annotations

import importlib.util
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus.config_schema import TTSConfig
from nexus.server.routes.pair import router as pair_router


class _FakeTTSModule:
    """Simulates `piper-tts` being absent from the environment."""

    def find_spec(self, name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "piper":
            return None
        return importlib.util.find_spec(name, *args, **kwargs)


def _build_client(cfg: Any) -> TestClient:
    """Minimal app carrying only the pair router — /status reads
    ``app.state.mutable_state["cfg"]`` and needs no auth middleware."""
    app = FastAPI()
    app.state.mutable_state = {"cfg": cfg}
    app.include_router(pair_router)
    return TestClient(app)


def _cfg(tts: TTSConfig) -> Any:
    return type("Cfg", (), {"tts": tts})()


def _get_status(client: TestClient) -> dict[str, Any]:
    resp = client.get("/status")
    assert resp.status_code == 200
    return resp.json()


def test_status_reports_tts_when_enabled_and_engine_available() -> None:
    cfg = _cfg(TTSConfig(enabled=True))
    body = _get_status(_build_client(cfg))
    assert body["features"]["tts"] is True


def test_status_reports_tts_false_when_disabled() -> None:
    cfg = _cfg(TTSConfig(enabled=False))
    body = _get_status(_build_client(cfg))
    assert body["features"]["tts"] is False


def test_status_reports_tts_false_when_piper_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enabled in settings but piper-tts not installed → 502 on
    /tts/synthesize, so /status must report False."""
    from nexus.tts import dispatch

    fake = _FakeTTSModule()
    monkeypatch.setattr(dispatch.importlib, "util", fake)
    cfg = _cfg(TTSConfig(enabled=True))
    body = _get_status(_build_client(cfg))
    assert body["features"]["tts"] is False


def test_status_still_carries_all_previous_fields() -> None:
    """Retrocompatibility: the flag is additive — existing fields intact."""
    cfg = _cfg(TTSConfig(enabled=True))
    body = _get_status(_build_client(cfg))
    for key in ("ok", "version", "machine_name", "tailnet_ip", "port", "features"):
        assert key in body
    assert body["ok"] is True
    for flag in ("browser", "browser_remote_control", "coordinator", "telegram",
                 "push_apns"):
        assert flag in body["features"]


# ── dispatch.is_available unit behaviour ──────────────────────────────────


def test_is_available_disabled_cfg() -> None:
    from nexus.tts.dispatch import is_available

    assert is_available(TTSConfig(enabled=False)) is False


def test_is_available_enabled_cfg() -> None:
    from nexus.tts.dispatch import is_available

    # The test venv has piper-tts installed (see test_tts.py's synthesis
    # route test), so enabled == operational here.
    assert is_available(TTSConfig(enabled=True)) is True


def test_is_available_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexus.tts import dispatch

    def _boom(_cfg: Any) -> None:
        raise RuntimeError("config exploded")

    monkeypatch.setattr(dispatch, "load_config", _boom)
    assert dispatch.is_available(None) is False
