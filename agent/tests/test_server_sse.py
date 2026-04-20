"""Integration tests for the SSE event stream + HITL ``/respond`` flow
+ ``/settings`` endpoints.

Runs a real uvicorn on a random port so the SSE response actually
streams incrementally — ``httpx.ASGITransport`` buffers the full
response body before yielding, which breaks any test that needs to
observe a streaming response while a separate request is in flight.

This is slower than the other server tests but the only reliable way
to exercise the ``/chat`` ↔ SSE ↔ ``/respond`` loop end-to-end.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

from nexus.agent.llm import (
    ChatMessage,
    ChatResponse,
    LLMProvider,
    StopReason,
    ToolCall,
    ToolSpec,
)
from nexus.agent.loop import Agent
from nexus.server.app import create_app
from nexus.server.session_store import SessionStore
from nexus.server.settings import SettingsStore
from nexus.skills.registry import SkillRegistry


class FakeProvider(LLMProvider):
    """Deterministic LLM stub: feeds a scripted list of ChatResponse
    objects one per ``chat()`` call. Lets integration tests drive the
    agent loop through specific tool-call paths without hitting a real
    provider."""

    def __init__(self, responses: list[ChatResponse]) -> None:
        self._responses = list(responses)
        self._i = 0

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
    ) -> ChatResponse:
        if self._i >= len(self._responses):
            # Unscripted call — return a benign stop so the loop exits
            # cleanly rather than hanging on an IndexError.
            return ChatResponse(content="", stop_reason=StopReason.STOP)
        r = self._responses[self._i]
        self._i += 1
        return r


def _free_port() -> int:
    """Ask the OS for an unused TCP port. Tiny race but acceptable for
    tests — we bind immediately after."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.asynccontextmanager
async def _serve(
    provider: LLMProvider, tmp_path: Path
) -> AsyncIterator[str]:
    """Start uvicorn in-process, yield the base URL, tear down cleanly.

    Each run gets a disposable SessionStore + SettingsStore under
    ``tmp_path`` so the real ``~/.nexus`` isn't touched."""
    registry = SkillRegistry(tmp_path / "skills")
    sessions = SessionStore(db_path=tmp_path / "sessions.sqlite")
    settings = SettingsStore(path=tmp_path / "settings.json")
    agent = Agent(provider=provider, registry=registry)
    app = create_app(
        agent=agent,
        registry=registry,
        sessions=sessions,
        settings_store=settings,
    )
    port = _free_port()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="error", access_log=False
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    # Wait for uvicorn to report started. `.started` flips after the
    # socket is listening; polling is simpler than threading events.
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.025)
    else:
        raise RuntimeError("uvicorn did not start within 5s")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5.0)


def _ask_user_response(prompt: str = "Proceed?") -> ChatResponse:
    """Scripted LLM response: ask_user tool call."""
    return ChatResponse(
        content=None,
        stop_reason=StopReason.TOOL_CALLS,
        tool_calls=[
            ToolCall(
                id="call_1",
                name="ask_user",
                arguments={
                    "prompt": prompt,
                    "kind": "confirm",
                    "timeout_seconds": 5,
                },
            )
        ],
    )


def _final_response(text: str) -> ChatResponse:
    return ChatResponse(content=text, stop_reason=StopReason.STOP)


async def _iter_sse_events(
    response: httpx.Response,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Parse the SSE byte stream into (event_kind, parsed_data) pairs.
    Handles the ``: comment`` opening line and blank-line event
    boundaries."""
    current_kind: str | None = None
    async for line in response.aiter_lines():
        if not line:
            current_kind = None
            continue
        if line.startswith(": "):
            continue  # SSE comment / keepalive
        if line.startswith("event: "):
            current_kind = line[len("event: "):].strip()
            continue
        if line.startswith("data: ") and current_kind is not None:
            yield current_kind, json.loads(line[len("data: "):])


# ── SSE stream wiring ────────────────────────────────────────────────


async def test_sse_stream_delivers_iter_events_during_turn(
    tmp_path: Path,
) -> None:
    """A normal turn emits ``iter`` and ``reply`` events to any live
    SSE subscriber. Base case — proves trace → publish → SSE works
    independent of HITL."""
    provider = FakeProvider([_final_response("done")])
    async with _serve(provider, tmp_path) as base:
        sid = "sess-plain"
        events: list[tuple[str, dict]] = []

        async def collect() -> None:
            async with httpx.AsyncClient(timeout=5.0) as c:
                async with c.stream(
                    "GET", f"{base}/chat/{sid}/events"
                ) as resp:
                    assert resp.status_code == 200
                    async for kind, data in _iter_sse_events(resp):
                        events.append((kind, data))
                        if kind == "reply":
                            return

        collector = asyncio.create_task(collect())
        await asyncio.sleep(0.1)  # let the SSE connection establish

        async with httpx.AsyncClient(timeout=5.0) as c:
            chat_resp = await c.post(
                f"{base}/chat", json={"message": "hi", "session_id": sid}
            )
            assert chat_resp.status_code == 200
            assert chat_resp.json()["reply"] == "done"

        await asyncio.wait_for(collector, timeout=3.0)

        kinds = [k for k, _ in events]
        assert "iter" in kinds
        assert kinds[-1] == "reply"


# ── Full HITL round-trip ─────────────────────────────────────────────


async def test_ask_user_full_round_trip(tmp_path: Path) -> None:
    """``/chat`` starts, parks on ask_user, SSE emits user_request,
    UI POSTs ``/respond``, ask_user returns the answer, LLM produces
    a final reply, ``/chat`` returns with that reply."""
    provider = FakeProvider([
        _ask_user_response("Proceed with the thing?"),
        _final_response("done."),
    ])
    async with _serve(provider, tmp_path) as base:
        sid = "sess-hitl"
        stream_events: list[tuple[str, dict]] = []
        request_id_future: asyncio.Future[str] = asyncio.get_event_loop().create_future()

        async def collect() -> None:
            async with httpx.AsyncClient(timeout=10.0) as c:
                async with c.stream(
                    "GET", f"{base}/chat/{sid}/events"
                ) as resp:
                    async for kind, data in _iter_sse_events(resp):
                        stream_events.append((kind, data))
                        if (
                            kind == "user_request"
                            and not request_id_future.done()
                        ):
                            request_id_future.set_result(data["request_id"])
                        if kind == "reply":
                            return

        collector = asyncio.create_task(collect())
        await asyncio.sleep(0.1)

        async def run_chat():
            async with httpx.AsyncClient(timeout=10.0) as c:
                return await c.post(
                    f"{base}/chat",
                    json={"message": "do it", "session_id": sid},
                )

        chat_task = asyncio.create_task(run_chat())

        # Wait for the user_request event to arrive.
        request_id = await asyncio.wait_for(request_id_future, timeout=5.0)

        # /chat is still pending at this point — prove it.
        assert not chat_task.done()

        async with httpx.AsyncClient(timeout=5.0) as c:
            resp_ack = await c.post(
                f"{base}/chat/{sid}/respond",
                json={"request_id": request_id, "answer": "yes"},
            )
            assert resp_ack.status_code == 204

        chat_resp = await asyncio.wait_for(chat_task, timeout=5.0)
        assert chat_resp.status_code == 200
        assert chat_resp.json()["reply"] == "done."

        await asyncio.wait_for(collector, timeout=3.0)

        kinds = [k for k, _ in stream_events]
        assert "user_request" in kinds
        assert "reply" in kinds


# ── /respond error cases ────────────────────────────────────────────


async def test_respond_returns_404_for_unknown_request(tmp_path: Path) -> None:
    """A user whose dialog timed out hits a 404 rather than a generic
    500. That matters for the UI: it lets the dialog show a specific
    message instead of a scary stack trace."""
    async with _serve(FakeProvider([]), tmp_path) as base:
        sid = "sess-404"
        # Materialize the session so the 404 is specifically about the
        # request, not the session.
        async with httpx.AsyncClient(timeout=3.0) as c:
            async with c.stream(
                "GET", f"{base}/chat/{sid}/events"
            ) as resp:
                assert resp.status_code == 200

            ack = await c.post(
                f"{base}/chat/{sid}/respond",
                json={"request_id": "nonexistent", "answer": "yes"},
            )
            assert ack.status_code == 404


async def test_respond_rejects_empty_request_id(tmp_path: Path) -> None:
    """Pydantic validation catches malformed bodies — saves a layer
    of handwritten guards inside the store."""
    async with _serve(FakeProvider([]), tmp_path) as base:
        async with httpx.AsyncClient(timeout=3.0) as c:
            ack = await c.post(
                f"{base}/chat/any/respond",
                json={"request_id": "", "answer": "yes"},
            )
            assert ack.status_code == 422  # validation error


# ── /settings endpoints ─────────────────────────────────────────────


async def test_get_settings_returns_defaults(tmp_path: Path) -> None:
    async with _serve(FakeProvider([]), tmp_path) as base:
        async with httpx.AsyncClient(timeout=3.0) as c:
            resp = await c.get(f"{base}/settings")
            assert resp.status_code == 200
            assert resp.json() == {"yolo_mode": False}


async def test_post_settings_persists_change(tmp_path: Path) -> None:
    async with _serve(FakeProvider([]), tmp_path) as base:
        async with httpx.AsyncClient(timeout=3.0) as c:
            resp = await c.post(
                f"{base}/settings", json={"yolo_mode": True}
            )
            assert resp.status_code == 200
            assert resp.json() == {"yolo_mode": True}

            # Round-trip GET confirms persistence.
            follow = await c.get(f"{base}/settings")
            assert follow.json() == {"yolo_mode": True}


async def test_post_settings_partial_update_preserves_other_fields(
    tmp_path: Path,
) -> None:
    """An omitted field is a no-op, not a reset to default. This
    contract matters when more settings land later — the UI will
    PATCH individual toggles."""
    async with _serve(FakeProvider([]), tmp_path) as base:
        async with httpx.AsyncClient(timeout=3.0) as c:
            await c.post(f"{base}/settings", json={"yolo_mode": True})
            # Empty body → no changes applied.
            resp = await c.post(f"{base}/settings", json={})
            assert resp.status_code == 200
            assert resp.json() == {"yolo_mode": True}


# ── streaming + HITL compose ─────────────────────────────────────────


async def test_ask_user_round_trip_via_chat_stream(tmp_path: Path) -> None:
    """Same round-trip as ``test_ask_user_full_round_trip`` but via
    ``/chat/stream`` — the path the UI actually uses. Proves the
    contextvar propagation works across the generator body so tools
    inside the turn can publish on the right session."""
    provider = FakeProvider([
        _ask_user_response("Proceed?"),
        _final_response("ok."),
    ])
    async with _serve(provider, tmp_path) as base:
        sid = "sess-stream"
        request_id_future: asyncio.Future[str] = asyncio.get_event_loop().create_future()

        async def watch_events() -> None:
            async with httpx.AsyncClient(timeout=10.0) as c:
                async with c.stream(
                    "GET", f"{base}/chat/{sid}/events"
                ) as resp:
                    async for kind, data in _iter_sse_events(resp):
                        if (
                            kind == "user_request"
                            and not request_id_future.done()
                        ):
                            request_id_future.set_result(data["request_id"])
                            return

        watcher = asyncio.create_task(watch_events())
        await asyncio.sleep(0.1)

        async def run_stream_chat() -> list[str]:
            events: list[str] = []
            async with httpx.AsyncClient(timeout=10.0) as c:
                async with c.stream(
                    "POST",
                    f"{base}/chat/stream",
                    json={"message": "do it", "session_id": sid},
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line.startswith("event: "):
                            events.append(line[len("event: ") :].strip())
            return events

        chat_task = asyncio.create_task(run_stream_chat())

        request_id = await asyncio.wait_for(request_id_future, timeout=5.0)
        await asyncio.wait_for(watcher, timeout=2.0)

        async with httpx.AsyncClient(timeout=5.0) as c:
            ack = await c.post(
                f"{base}/chat/{sid}/respond",
                json={"request_id": request_id, "answer": "yes"},
            )
            assert ack.status_code == 204

        events = await asyncio.wait_for(chat_task, timeout=5.0)
        # /chat/stream emits `done` when the turn completes. Presence
        # is all we need to know — its arrival means the ask_user
        # future resolved and the agent made progress past the HITL
        # pause.
        assert "done" in events


async def test_yolo_through_stream_does_not_open_dialog(tmp_path: Path) -> None:
    """YOLO is wired into ``AskUserHandler`` via a getter on the
    SettingsStore. Flipping ``yolo_mode`` via the endpoint must make
    the next ``ask_user(confirm)`` inside a streaming turn auto-resolve
    without ever emitting a ``user_request`` event."""
    provider = FakeProvider([
        _ask_user_response("Proceed?"),
        _final_response("ok."),
    ])
    async with _serve(provider, tmp_path) as base:
        async with httpx.AsyncClient(timeout=3.0) as c:
            await c.post(f"{base}/settings", json={"yolo_mode": True})

        sid = "sess-yolo-stream"
        kinds_seen: list[str] = []

        async def collect() -> None:
            async with httpx.AsyncClient(timeout=10.0) as c:
                async with c.stream(
                    "GET", f"{base}/chat/{sid}/events"
                ) as resp:
                    async for kind, _data in _iter_sse_events(resp):
                        kinds_seen.append(kind)
                        if kind == "reply":
                            return

        collector = asyncio.create_task(collect())
        await asyncio.sleep(0.1)

        async with httpx.AsyncClient(timeout=10.0) as c:
            async with c.stream(
                "POST",
                f"{base}/chat/stream",
                json={"message": "do it", "session_id": sid},
            ) as resp:
                async for _line in resp.aiter_lines():
                    pass

        await asyncio.wait_for(collector, timeout=3.0)

        # YOLO path must emit user_request_auto, never user_request.
        assert "user_request_auto" in kinds_seen
        assert "user_request" not in kinds_seen
