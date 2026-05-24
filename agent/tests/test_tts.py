"""TTS engine + route smoke tests.

The engine is fixed (Piper). Tests focus on:
  - Voice resolution: explicit override > auto-detect > language default
  - The route returns 502 when TTS is disabled, 200 with audio bytes
    when synthesis succeeds, 422 on empty text
  - voice_setup correctly skips when voices are present
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus.config_schema import TTSConfig
from nexus.tts import TTSError, resolve_voice_for_text, synthesize
from nexus.tts.base import SynthResult
from nexus.tts.dispatch import DEFAULT_VOICES


def _build_app() -> FastAPI:
    """Build a minimal FastAPI app with an agent stub on state.

    The /tts/synthesize route depends on an agent (for the optional
    summarize-if-long pass via the LLM). Most tests don't exercise that
    path, but the dependency is wired regardless, so we hand it an
    object with a no-op _resolve_provider.
    """
    from nexus.server.routes.tts import router

    class _StubAgent:
        def _resolve_provider(self, _model_id):
            return None, None

    app = FastAPI()
    app.state.agent = _StubAgent()
    app.include_router(router)
    return app


def _patch_dispatch_load(monkeypatch: pytest.MonkeyPatch, cfg: TTSConfig) -> None:
    """`dispatch.py` binds `load` at import time so we patch the
    re-exported reference, not the source module."""
    from nexus.tts import dispatch
    monkeypatch.setattr(dispatch, "load_config", lambda: type("C", (), {"tts": cfg})())


# ── Voice resolution ───────────────────────────────────────────────────────


def test_auto_detect_picks_portuguese_voice() -> None:
    out = resolve_voice_for_text("Olá mundo, como você está hoje? Tudo bem?")
    assert out == DEFAULT_VOICES["pt"]


def test_auto_detect_picks_english_voice() -> None:
    out = resolve_voice_for_text("Hello world, how are you doing today?")
    assert out == DEFAULT_VOICES["en"]


def test_short_text_falls_back_to_english() -> None:
    # Too short for langdetect → English fallback.
    assert resolve_voice_for_text("ok") == DEFAULT_VOICES["en"]


def test_empty_text_falls_back_to_english() -> None:
    assert resolve_voice_for_text("") == DEFAULT_VOICES["en"]


# ── Dispatch + route ───────────────────────────────────────────────────────


async def test_synthesize_disabled_raises() -> None:
    cfg = TTSConfig(enabled=False)
    with pytest.raises(TTSError):
        await synthesize("hello", cfg=cfg)


def test_route_synthesize_disabled_returns_502(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = TTSConfig(enabled=False)
    _patch_dispatch_load(monkeypatch, cfg)
    client = TestClient(_build_app())
    r = client.post("/tts/synthesize", json={"text": "hi"})
    assert r.status_code == 502


def test_route_rejects_empty_text(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = TTSConfig(enabled=True)
    _patch_dispatch_load(monkeypatch, cfg)
    client = TestClient(_build_app())
    r = client.post("/tts/synthesize", json={"text": ""})
    assert r.status_code == 422  # Pydantic min_length=1


def test_route_synthesize_returns_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: route invokes piper.synthesize and forwards the bytes."""
    cfg = TTSConfig(enabled=True)
    _patch_dispatch_load(monkeypatch, cfg)
    from nexus.tts import dispatch
    fake = AsyncMock(return_value=SynthResult(audio=b"\x00\x01\x02", mime="audio/wav"))
    monkeypatch.setattr(dispatch._piper, "synthesize", fake)

    client = TestClient(_build_app())
    r = client.post("/tts/synthesize", json={"text": "hello"})
    assert r.status_code == 200
    assert r.content == b"\x00\x01\x02"
    assert r.headers["content-type"].startswith("audio/wav")
    fake.assert_awaited_once()
    # No summary asked for → no header.
    assert "X-TTS-Summarized" not in r.headers


def test_route_synthesize_summarizes_when_flag_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When summarize_if_long is set and the text is over the cap, the
    route prefixes the synthesized audio with a 'Here's a summary' line
    and tags the response with X-TTS-Summarized."""
    cfg = TTSConfig(enabled=True)
    _patch_dispatch_load(monkeypatch, cfg)
    from nexus.tts import dispatch
    from nexus.server.routes import tts as tts_route

    fake_synth = AsyncMock(return_value=SynthResult(audio=b"abc", mime="audio/wav"))
    monkeypatch.setattr(dispatch._piper, "synthesize", fake_synth)
    fake_summary = AsyncMock(return_value="A short summary here.")
    monkeypatch.setattr(tts_route, "summarize_long_text", fake_summary)

    long_text = "word " * 600  # 600 words → over the default 500 cap
    client = TestClient(_build_app())
    r = client.post("/tts/synthesize", json={
        "text": long_text, "summarize_if_long": True,
    })
    assert r.status_code == 200
    assert r.headers.get("X-TTS-Summarized") == "1"
    fake_summary.assert_awaited_once()
    # The synthesized text should start with the language-aware preamble.
    sent_to_piper = fake_synth.await_args[0][0]
    assert "summary" in sent_to_piper.lower()


def test_route_synthesize_skips_summary_when_short(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Short texts pass through verbatim even when the flag is set."""
    cfg = TTSConfig(enabled=True)
    _patch_dispatch_load(monkeypatch, cfg)
    from nexus.tts import dispatch
    from nexus.server.routes import tts as tts_route

    fake_synth = AsyncMock(return_value=SynthResult(audio=b"abc", mime="audio/wav"))
    monkeypatch.setattr(dispatch._piper, "synthesize", fake_synth)
    fake_summary = AsyncMock(return_value="should not be called")
    monkeypatch.setattr(tts_route, "summarize_long_text", fake_summary)

    client = TestClient(_build_app())
    r = client.post("/tts/synthesize", json={
        "text": "short text here", "summarize_if_long": True,
    })
    assert r.status_code == 200
    assert "X-TTS-Summarized" not in r.headers
    fake_summary.assert_not_awaited()


# ── voice_setup bootstrap ──────────────────────────────────────────────────


def _patch_voice_setup_cfg(monkeypatch: pytest.MonkeyPatch, cfg: TTSConfig) -> None:
    """voice_setup binds `load` at import time — patch the alias directly."""
    from nexus.tts import voice_setup
    monkeypatch.setattr(
        voice_setup, "load_config",
        lambda: type("C", (), {"tts": cfg})(),
    )


def test_voices_already_present_true_when_files_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = TTSConfig(voices_dir=str(tmp_path))
    for vid in DEFAULT_VOICES.values():
        (tmp_path / f"{vid}.onnx").write_bytes(b"x")
        (tmp_path / f"{vid}.onnx.json").write_text("{}")
    _patch_voice_setup_cfg(monkeypatch, cfg)
    from nexus.tts.voice_setup import voices_already_present
    assert voices_already_present() is True


def test_voices_already_present_false_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = TTSConfig(voices_dir=str(tmp_path))
    _patch_voice_setup_cfg(monkeypatch, cfg)
    from nexus.tts.voice_setup import voices_already_present
    assert voices_already_present() is False
