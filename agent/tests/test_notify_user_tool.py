"""NotifyUserHandler routes the agent-supplied message through voice_ack.

When input_mode is voice, the message is published with audio bytes
(synthesized via Piper). When text, the event still goes through with
audio_b64=None — the UI surfaces it as a toast.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from nexus.agent.context import CURRENT_SESSION_ID
from nexus.agent.notify_user_tool import NotifyUserHandler
from nexus.tts.base import SynthResult


class _StubStore:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, dict[str, Any]]] = []
        self._latest_input_mode: dict[str, str] = {}

    def publish(self, session_id: str, event: Any) -> None:
        self.published.append((session_id, event.kind, dict(event.data)))


async def test_notify_user_voice_publishes_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Voice input → message goes through Piper → audio_b64 in payload."""
    from nexus import voice_ack
    fake_synth = AsyncMock(return_value=SynthResult(audio=b"\x01\x02", mime="audio/wav"))
    monkeypatch.setattr(voice_ack, "synthesize", fake_synth)

    store = _StubStore()
    store._latest_input_mode["sess-1"] = "voice"
    handler = NotifyUserHandler(session_store=store)

    token = CURRENT_SESSION_ID.set("sess-1")
    try:
        result = await handler.invoke({"message": "Tô buscando, segura aí."})
        # The tool returns immediately; let the background task publish.
        for _ in range(20):
            if store.published:
                break
            await asyncio.sleep(0.01)
    finally:
        CURRENT_SESSION_ID.reset(token)

    assert json.loads(result) == {"ok": True}
    assert len(store.published) == 1
    sid, kind, data = store.published[0]
    assert sid == "sess-1"
    assert kind == "voice_ack"
    assert data["kind"] == "notify"
    assert data["transcript"] == "Tô buscando, segura aí."
    # Voice input means audio bytes should be present.
    assert data["audio_b64"] is not None


async def test_notify_user_text_publishes_without_audio() -> None:
    """Text input → no Piper call; event still published for the toast."""
    store = _StubStore()
    store._latest_input_mode["sess-1"] = "text"
    handler = NotifyUserHandler(session_store=store)

    token = CURRENT_SESSION_ID.set("sess-1")
    try:
        result = await handler.invoke({"message": "Looking that up — about a minute."})
        for _ in range(20):
            if store.published:
                break
            await asyncio.sleep(0.01)
    finally:
        CURRENT_SESSION_ID.reset(token)

    assert json.loads(result) == {"ok": True}
    assert len(store.published) == 1
    _, _, data = store.published[0]
    assert data["kind"] == "notify"
    # No audio bytes for text input.
    assert data["audio_b64"] is None
    assert data["transcript"] == "Looking that up — about a minute."


async def test_notify_user_rejects_empty_message() -> None:
    store = _StubStore()
    handler = NotifyUserHandler(session_store=store)
    token = CURRENT_SESSION_ID.set("sess-1")
    try:
        result = await handler.invoke({"message": "  "})
    finally:
        CURRENT_SESSION_ID.reset(token)
    assert json.loads(result) == {"ok": False, "error": "message required"}
    assert store.published == []


async def test_notify_user_no_session_returns_error() -> None:
    """Outside a chat turn (no CURRENT_SESSION_ID), the tool refuses."""
    store = _StubStore()
    handler = NotifyUserHandler(session_store=store)
    # Don't set CURRENT_SESSION_ID — should fall through to error.
    result = await handler.invoke({"message": "hi"})
    parsed = json.loads(result)
    assert parsed["ok"] is False
    assert "no current session" in parsed["error"]
