"""Shared parked-HITL resume service — ``nexus.server.services.hitl_resume``.

Covers ``prepare_parked_resume`` (404 / session mismatch / duplicate /
first-answer-wins) and ``drive_parked_resume`` (event passthrough,
usage + persistence side effects, terminal ``turn_settled`` bus event,
task-registry cleanup, synthetic error events on failure).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from nexus.agent.context import CURRENT_SESSION_ID
from nexus.agent.llm import ChatMessage, Role
from nexus.server.services.hitl_resume import drive_parked_resume, prepare_parked_resume
from nexus.server.session_store import SessionStore


def _park(store: SessionStore, sid: str, request_id: str = "req1") -> None:
    store.persist_hitl_pending(
        session_id=sid,
        request_id=request_id,
        tool_call_id="tc1",
        kind="text",
        prompt="q?",
        choices=None,
        fields=None,
        form_title=None,
        form_description=None,
        default=None,
        timeout_seconds=300,
    )


class FakeAgent:
    def __init__(self, events: list[dict[str, Any]] | None = None, exc: Exception | None = None):
        self.events = events or []
        self.exc = exc
        self.seen_context: str | None = None

    async def continue_after_hitl(self, *, session_id, request_id, answer):
        self.seen_context = CURRENT_SESSION_ID.get()
        if self.exc is not None:
            raise self.exc
        for ev in self.events:
            yield ev


async def test_prepare_no_row_returns_none(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "s.sqlite")
    assert await prepare_parked_resume(store, session_id="s1", request_id="nope", raw_answer="x") is None


async def test_prepare_wrong_session_raises(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "s.sqlite")
    s = store.create()
    _park(store, s.id)
    with pytest.raises(ValueError):
        await prepare_parked_resume(store, session_id="other", request_id="req1", raw_answer="x")


async def test_prepare_decodes_json_and_marks_answered(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "s.sqlite")
    s = store.create()
    _park(store, s.id)
    prepared = await prepare_parked_resume(
        store, session_id=s.id, request_id="req1", raw_answer='{"a": 1}'
    )
    assert prepared is not None and prepared.duplicate is False
    assert prepared.decoded == {"a": 1}
    assert store.get_hitl_pending("req1")["status"] == "answered"


async def test_prepare_duplicate_flags_replay(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "s.sqlite")
    s = store.create()
    _park(store, s.id)
    first = await prepare_parked_resume(store, session_id=s.id, request_id="req1", raw_answer="one")
    assert first is not None and not first.duplicate
    second = await prepare_parked_resume(store, session_id=s.id, request_id="req1", raw_answer="two")
    assert second is not None and second.duplicate
    # The recorded answer is the first one.
    assert json.loads(second.answer_json or "") == "one"


async def test_drive_yields_events_and_finalizes(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "s.sqlite")
    s = store.create()
    _park(store, s.id)
    prepared = await prepare_parked_resume(store, session_id=s.id, request_id="req1", raw_answer="ans")
    assert prepared is not None

    agent = FakeAgent(
        events=[
            {"type": "delta", "text": "hello"},
            {
                "type": "done",
                "session_id": s.id,
                "reply": "hello",
                "messages": [ChatMessage(role=Role.ASSISTANT, content="hello")],
                "usage": {"model": "m", "input_tokens": 5, "output_tokens": 7, "tool_calls": 1},
            },
        ]
    )
    registry: dict[str, asyncio.Task] = {}
    usage_calls: list[dict[str, Any]] = []
    orig_bump = store.bump_usage

    def _record_bump(session_id, **kwargs):
        usage_calls.append({"session_id": session_id, **kwargs})
        return orig_bump(session_id, **kwargs)

    store.bump_usage = _record_bump  # type: ignore[method-assign]

    seen: list[str] = []
    async for ev in drive_parked_resume(agent, store, prepared, task_registry=registry):
        seen.append(str(ev.get("type")))

    assert seen == ["delta", "done"]
    # Session contextvar was live while the agent ran.
    assert agent.seen_context == s.id
    # Contextvar restored + registry cleaned up.
    assert CURRENT_SESSION_ID.get() is None
    assert s.id not in registry
    # Reply persisted.
    hist = store.get(s.id).history
    assert hist and hist[-1].content == "hello"
    # Usage recorded.
    assert usage_calls and usage_calls[0]["input_tokens"] == 5
    # Terminal bus marker for streamers.
    assert any(ev.kind == "turn_settled" for _, ev in store._replay.get(s.id, []))


async def test_drive_error_yields_synthetic_events_and_publishes(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "s.sqlite")
    s = store.create()
    _park(store, s.id)
    prepared = await prepare_parked_resume(store, session_id=s.id, request_id="req1", raw_answer="ans")
    assert prepared is not None

    agent = FakeAgent(exc=RuntimeError("boom"))
    seen: list[str] = []
    async for ev in drive_parked_resume(agent, store, prepared):
        seen.append(str(ev.get("type")))

    assert seen == ["error", "done"]
    replay = store._replay.get(s.id, [])
    assert any(ev.kind == "error" and "boom" in str(ev.data) for _, ev in replay)
    assert any(ev.kind == "turn_settled" for _, ev in replay)


# ── HTTP route (refactored onto the shared service) ─────────────────────


async def test_http_resume_route_streams_and_is_idempotent(tmp_path: Path) -> None:
    """POST /chat/{sid}/hitl/{rid}/answer keeps its SSE contract after the
    refactor onto the shared service: first answer streams the resumed
    turn (delta + done), a replaying duplicate returns one done frame."""
    import contextlib
    import socket

    import httpx
    import uvicorn

    from nexus.agent.loop import Agent
    from nexus.server.app import create_app
    from nexus.server.settings import SettingsStore
    from nexus.skills.registry import SkillRegistry
    from test_server_sse import FakeProvider, _final_response, _iter_sse_events

    provider = FakeProvider([_final_response("resumed via http")])
    registry = SkillRegistry(tmp_path / "skills")
    sessions = SessionStore(db_path=tmp_path / "sessions.sqlite")
    agent = Agent(provider=provider, registry=registry)
    app = create_app(
        agent=agent,
        registry=registry,
        sessions=sessions,
        settings_store=SettingsStore(path=tmp_path / "settings.json"),
    )

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False)
    )
    task = asyncio.create_task(server.serve())
    for _ in range(1200):
        if server.started:
            break
        await asyncio.sleep(0.025)
    else:  # pragma: no cover — startup guard
        raise RuntimeError("uvicorn did not start")

    try:
        s = sessions.create()
        sessions.persist_hitl_pending(
            session_id=s.id,
            request_id="reqH",
            tool_call_id="tc1",
            kind="text",
            prompt="Deferred?",
            choices=None,
            fields=None,
            form_title=None,
            form_description=None,
            default=None,
            timeout_seconds=300,
        )
        sessions.update_hitl_pending_snapshot(
            "reqH", json.dumps([{"role": "user", "content": "hi"}])
        )

        base = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(timeout=10.0) as c:
            async with c.stream(
                "POST",
                f"{base}/chat/{s.id}/hitl/reqH/answer",
                json={"request_id": "reqH", "answer": "my answer"},
            ) as resp:
                assert resp.status_code == 200
                events = [ev async for ev in _iter_sse_events(resp)]
        kinds = [k for k, _ in events]
        assert "delta" in kinds
        assert kinds[-1] == "done"
        assert any(d.get("reply") == "resumed via http" for k, d in events if k == "done")

        # Replay from a second client → single idempotent done frame.
        async with httpx.AsyncClient(timeout=10.0) as c:
            async with c.stream(
                "POST",
                f"{base}/chat/{s.id}/hitl/reqH/answer",
                json={"request_id": "reqH", "answer": "other answer"},
            ) as resp:
                assert resp.status_code == 200
                dup = [ev async for ev in _iter_sse_events(resp)]
        assert [k for k, _ in dup] == ["done"]
        assert dup[0][1].get("duplicate") is True
        assert json.loads(dup[0][1]["answer"]) == "my answer"
    finally:
        server.should_exit = True
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5.0)
