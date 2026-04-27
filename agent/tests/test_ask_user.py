"""Tests for SessionStore pub/sub + pending-futures and the
AskUserHandler tool that rides on top.

Two layers of tests here:

* Store-level: publish/subscribe fanout, register/resolve/cancel
  pending, reset-cancels-pending.
* Tool-level: AskUserHandler happy path, timeout, YOLO auto-confirm,
  missing session context, invalid arguments.

Full SSE + HTTP round-trip through the FastAPI app lives in
``test_server_sse.py`` — here we stay in-process.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from nexus.agent.ask_user_tool import (
    ASK_USER_TOOL,
    AskUserHandler,
)
from nexus.agent.context import CURRENT_SESSION_ID
from nexus.server.events import SessionEvent
from nexus.server.session_store import SessionStore


def _store(tmp_path: Path) -> SessionStore:
    # Every test gets its own SQLite file so state doesn't leak between
    # them. Nexus's SessionStore requires a Path at construction.
    return SessionStore(db_path=tmp_path / "sessions.sqlite")


# ── SessionStore: pub/sub + pending ──────────────────────────────────


async def test_publish_reaches_subscriber(tmp_path: Path) -> None:
    store = _store(tmp_path)
    s = store.create()

    received: list[SessionEvent] = []

    async def consumer() -> None:
        async for event in store.subscribe(s.id):
            received.append(event)
            return

    task = asyncio.create_task(consumer())
    # Yield so the subscriber registers its queue before we publish.
    await asyncio.sleep(0)
    store.publish(s.id, SessionEvent(kind="iter", data={"n": 1}))
    await asyncio.wait_for(task, timeout=1.0)

    assert len(received) == 1
    assert received[0].kind == "iter"
    assert received[0].data == {"n": 1}


async def test_publish_fans_out_to_multiple_subscribers(tmp_path: Path) -> None:
    """Two UI tabs open on the same session should both see events.
    Tests the fanout list, not just a single queue."""
    store = _store(tmp_path)
    s = store.create()

    received_a: list[SessionEvent] = []
    received_b: list[SessionEvent] = []

    async def consume(into: list[SessionEvent]) -> None:
        async for event in store.subscribe(s.id):
            into.append(event)
            if len(into) == 2:
                return

    ta = asyncio.create_task(consume(received_a))
    tb = asyncio.create_task(consume(received_b))
    await asyncio.sleep(0)
    store.publish(s.id, SessionEvent("iter", {"n": 1}))
    store.publish(s.id, SessionEvent("reply", {"text": "hi"}))

    await asyncio.wait_for(ta, timeout=1.0)
    await asyncio.wait_for(tb, timeout=1.0)

    assert [e.kind for e in received_a] == ["iter", "reply"]
    assert [e.kind for e in received_b] == ["iter", "reply"]


async def test_publish_to_unknown_session_is_silent(tmp_path: Path) -> None:
    """Publishing before anyone subscribes (or to a deleted session)
    must not crash — early trace events happen routinely before the
    UI has opened its EventSource."""
    store = _store(tmp_path)
    store.publish("nowhere", SessionEvent("iter", {}))
    # Getting here is the test — no exception raised.


async def test_register_pending_returns_resolvable_future(tmp_path: Path) -> None:
    store = _store(tmp_path)
    s = store.create()

    fut = store.register_pending(s.id, "req-1")
    assert not fut.done()
    assert store.resolve_pending(s.id, "req-1", "yes") is True
    assert fut.result() == "yes"


async def test_resolve_pending_returns_false_when_unknown(tmp_path: Path) -> None:
    """The /respond endpoint relies on this to 404 stale clicks."""
    store = _store(tmp_path)
    s = store.create()
    assert store.resolve_pending(s.id, "nowhere", "yes") is False


async def test_resolve_pending_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    s = store.create()
    store.register_pending(s.id, "req-1")

    assert store.resolve_pending(s.id, "req-1", "yes") is True
    # Second resolve must not double-fire or crash.
    assert store.resolve_pending(s.id, "req-1", "yes") is False


async def test_register_pending_rejects_duplicate(tmp_path: Path) -> None:
    """Same request_id twice would let a stale /respond clobber a
    fresh dialog. We want a hard error, not silent replace."""
    store = _store(tmp_path)
    s = store.create()
    store.register_pending(s.id, "req-1")
    with pytest.raises(ValueError, match="already pending"):
        store.register_pending(s.id, "req-1")


async def test_cancel_pending_cancels_future(tmp_path: Path) -> None:
    store = _store(tmp_path)
    s = store.create()
    fut = store.register_pending(s.id, "req-1")

    assert store.cancel_pending(s.id, "req-1") is True
    with pytest.raises(asyncio.CancelledError):
        fut.result()


async def test_session_reset_cancels_pending(tmp_path: Path) -> None:
    """If the user hits 'reset' while a dialog is open, the pending
    Future must be cancelled so ask_user returns cleanly rather than
    hanging forever."""
    store = _store(tmp_path)
    s = store.create()
    fut = store.register_pending(s.id, "req-1")

    store.reset(s.id)

    assert fut.cancelled()


async def test_session_delete_cancels_pending(tmp_path: Path) -> None:
    """Deleting a session mid-dialog should not leave a dangling
    Future that the ask_user coroutine awaits forever."""
    store = _store(tmp_path)
    s = store.create()
    fut = store.register_pending(s.id, "req-1")

    store.delete(s.id)

    assert fut.cancelled()


# ── AskUserHandler ───────────────────────────────────────────────────


def _bind_session(store: SessionStore):
    """Create a session + set the contextvar + return (session_id, token).

    Caller must ``CURRENT_SESSION_ID.reset(token)`` when done; we
    return the token so tests can control the lifecycle."""
    s = store.create()
    token = CURRENT_SESSION_ID.set(s.id)
    return s.id, token


def test_tool_spec_shape() -> None:
    assert ASK_USER_TOOL.name == "ask_user"
    props = ASK_USER_TOOL.parameters["properties"]
    assert set(props.keys()) >= {
        "prompt",
        "kind",
        "choices",
        "default",
        "timeout_seconds",
    }
    assert ASK_USER_TOOL.parameters["required"] == ["prompt"]


async def test_happy_path_yes_confirm(tmp_path: Path) -> None:
    """End-to-end in-process: handler publishes a user_request, we
    pluck the request_id from the event stream, resolve the Future,
    and assert ask_user returns the answer."""
    store = _store(tmp_path)
    session_id, token = _bind_session(store)
    try:
        handler = AskUserHandler(session_store=store)

        async def approver() -> None:
            async for ev in store.subscribe(session_id):
                if ev.kind == "user_request":
                    rid = ev.data["request_id"]
                    store.resolve_pending(session_id, rid, "yes")
                    return

        consumer = asyncio.create_task(approver())
        await asyncio.sleep(0)  # let subscribe register

        result = await handler.invoke(
            {"prompt": "Proceed?", "kind": "confirm", "timeout_seconds": 5}
        )
        await asyncio.wait_for(consumer, timeout=2.0)

        assert result.ok and result.answer == "yes"
        assert result.timed_out is False
        assert result.kind == "confirm"
    finally:
        CURRENT_SESSION_ID.reset(token)


async def test_timeout_returns_sentinel_and_cancels_future(tmp_path: Path) -> None:
    """When the user doesn't respond, ask_user returns a special
    answer so the agent can tell 'user said no' from 'user never
    answered'. The Future is cleaned from the pending registry."""
    store = _store(tmp_path)
    session_id, token = _bind_session(store)
    try:
        handler = AskUserHandler(session_store=store)

        result = await handler.invoke(
            {"prompt": "Hello?", "kind": "confirm", "timeout_seconds": 0.05}
        )

        assert result.timed_out is True
        assert result.answer == "__timeout__"
        assert result.ok is True
    finally:
        CURRENT_SESSION_ID.reset(token)


async def test_missing_session_context_is_reported_clearly(tmp_path: Path) -> None:
    """Calling ask_user outside /chat (no contextvar set) must return
    a descriptive error so the model doesn't think the user refused."""
    store = _store(tmp_path)
    handler = AskUserHandler(session_store=store)

    result = await handler.invoke({"prompt": "Proceed?"})

    assert not result.ok
    assert result.error is not None
    assert "session" in result.error.lower()


async def test_no_session_store_is_reported_clearly() -> None:
    """An Agent built without a SessionStore still exposes the tool
    type, but the handler should refuse with a helpful error."""
    handler = AskUserHandler(session_store=None)
    result = await handler.invoke({"prompt": "Proceed?"})
    assert not result.ok
    assert result.error is not None
    assert "unavailable" in result.error


async def test_yolo_mode_auto_confirms_without_dialog(tmp_path: Path) -> None:
    """With YOLO on, a kind=confirm ask_user returns 'yes' immediately.
    An auto-event is still published so the transcript shows the
    decision — silent auto-approve would be unauditable."""
    store = _store(tmp_path)
    session_id, token = _bind_session(store)
    try:
        handler = AskUserHandler(
            session_store=store, yolo_mode_getter=lambda: True
        )

        events: list[SessionEvent] = []

        async def capture() -> None:
            async for ev in store.subscribe(session_id):
                events.append(ev)
                return

        task = asyncio.create_task(capture())
        await asyncio.sleep(0)

        result = await handler.invoke({"prompt": "rm -rf /", "kind": "confirm"})
        await asyncio.wait_for(task, timeout=1.0)

        assert result.answer == "yes"
        assert result.ok and not result.timed_out
        assert events and events[0].kind == "user_request_auto"
        assert events[0].data["answer"] == "yes"
    finally:
        CURRENT_SESSION_ID.reset(token)


async def test_yolo_does_not_auto_answer_choice_or_text(tmp_path: Path) -> None:
    """YOLO is a trust-the-agent escape hatch for confirms — it does
    NOT apply to choice/text, which need real user input."""
    store = _store(tmp_path)
    session_id, token = _bind_session(store)
    try:
        handler = AskUserHandler(
            session_store=store, yolo_mode_getter=lambda: True
        )

        # With a tight timeout we verify the handler actually waits
        # rather than short-circuiting.
        result = await handler.invoke(
            {
                "prompt": "Pick",
                "kind": "choice",
                "choices": ["a", "b"],
                "timeout_seconds": 0.05,
            }
        )
        assert result.timed_out is True

        # Same check for text.
        result_text = await handler.invoke(
            {"prompt": "Name?", "kind": "text", "timeout_seconds": 0.05}
        )
        assert result_text.timed_out is True
    finally:
        CURRENT_SESSION_ID.reset(token)


async def test_choice_requires_choices_array(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _bind_session(store)  # sets contextvar — we're asserting arg validation
    # Ensure we restore after even though we don't care about leaking here.
    try:
        handler = AskUserHandler(session_store=store)
        result = await handler.invoke({"prompt": "?", "kind": "choice"})
        assert not result.ok and result.error is not None
        assert "choices" in result.error
    finally:
        # Reset any contextvar leak so other tests aren't polluted.
        CURRENT_SESSION_ID.set(None)


async def test_to_text_is_json_round_trip(tmp_path: Path) -> None:
    """Stable serialization for the tool-result message."""
    store = _store(tmp_path)
    session_id, token = _bind_session(store)
    try:
        handler = AskUserHandler(session_store=store)
        result = await handler.invoke(
            {"prompt": "Hello?", "kind": "confirm", "timeout_seconds": 0.05}
        )
        parsed = json.loads(result.to_text())
        assert parsed["timed_out"] is True
        assert parsed["answer"] == "__timeout__"
    finally:
        CURRENT_SESSION_ID.reset(token)


async def test_form_supersedes_existing_parked_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new form ask_user on a session with a stale parked form must
    cancel the stale row and broadcast user_request_cancelled, so the
    UI replaces the old form instead of stacking another one.

    Repros the "varios pedidos do mesmo formulario" symptom: an agent
    retry loop that re-asks the same form would otherwise leave one
    parked row per call, each republished on every reconnect.
    """
    from nexus.agent import ask_user_tool as module

    store = _store(tmp_path)
    session_id, token = _bind_session(store)
    try:
        # Plant a stale parked form for this session, mimicking the row
        # that ``_ask_parkable`` would have written on a previous call.
        stale_rid = "stale-rid"
        store.persist_hitl_pending(
            session_id=session_id,
            request_id=stale_rid,
            tool_call_id="tc-1",
            kind="form",
            prompt="old form",
            choices=None,
            fields=[{"name": "url", "type": "text"}],
            form_title="Old",
            form_description=None,
            default=None,
            timeout_seconds=300,
        )
        assert store.get_hitl_pending(stale_rid)["status"] == "parked"

        # Make the new ask_user park almost immediately so we can observe
        # the cancel + new publish without burning seconds in the test.
        monkeypatch.setattr(module, "_PARK_THRESHOLD_SECONDS", 0.05)

        events: list[SessionEvent] = []

        async def capture() -> None:
            async for ev in store.subscribe(session_id):
                events.append(ev)
                # Stop once we've seen the cancel + the new request.
                kinds = [e.kind for e in events]
                if (
                    "user_request_cancelled" in kinds
                    and "user_request" in kinds
                ):
                    return

        consumer = asyncio.create_task(capture())
        await asyncio.sleep(0)  # let the subscriber register

        handler = AskUserHandler(session_store=store)
        result = await handler.invoke(
            {
                "prompt": "What URL?",
                "kind": "form",
                "fields": [{"name": "url", "type": "text"}],
                "title": "New",
                "timeout_seconds": 5,
            }
        )

        await asyncio.wait_for(consumer, timeout=2.0)

        # Stale row was cancelled (not still 'parked').
        stale_row = store.get_hitl_pending(stale_rid)
        assert stale_row is not None
        assert stale_row["status"] == "cancelled"

        # The cancel event names the stale rid with reason="superseded".
        cancels = [e for e in events if e.kind == "user_request_cancelled"]
        assert any(
            e.data.get("request_id") == stale_rid
            and e.data.get("reason") == "superseded"
            for e in cancels
        ), f"expected superseded cancel for {stale_rid}, got {cancels}"

        # The new ask_user still parked normally and returned a sentinel.
        assert result.ok
        assert result.answer is not None
        assert result.answer.startswith("__parked__:")
        new_rid = result.answer.removeprefix("__parked__:")
        assert new_rid != stale_rid
    finally:
        CURRENT_SESSION_ID.reset(token)
