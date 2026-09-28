"""HITL prompt routing — Telegram-bound sessions suppress UI surfaces.

A user_request on a session that belongs to a Telegram chat must be
delivered by the Telegram forwarder ONLY: the /notifications SSE (UI
approval dialog), /notifications/pending (dialog recovery), and web push
all skip it. The bell history row is still written (audit).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from nexus.server.events import SessionEvent
from nexus.server.routes.notifications import (
    notifications_events,
    notifications_pending,
)
from nexus.server.session_store import SessionStore
from nexus.telegram.bindings import TelegramBindingStore


def _isolate_bindings(monkeypatch: Any, tmp_path: Path) -> None:
    """Point session_is_telegram_routed at this test's sessions DB."""

    class PatchedStore(TelegramBindingStore):
        def __init__(self, db_path: Any = None) -> None:
            super().__init__(tmp_path / "sessions.sqlite")

    monkeypatch.setattr("nexus.telegram.bindings.TelegramBindingStore", PatchedStore)


def _user_request(rid: str) -> SessionEvent:
    return SessionEvent(
        kind="user_request",
        data={"request_id": rid, "prompt": f"prompt {rid}", "kind": "confirm"},
    )


async def test_notifications_sse_skips_telegram_routed(
    tmp_path: Path, monkeypatch: Any
) -> None:
    _isolate_bindings(monkeypatch, tmp_path)
    store = SessionStore(db_path=tmp_path / "sessions.sqlite")
    bindings = TelegramBindingStore(tmp_path / "sessions.sqlite")

    routed = store.create()
    other = store.create()
    bindings.upsert(
        chat_id=1, thread_id=0, kind="dm",
        project_id=None, active_session_id=routed.id,
    )

    resp = await notifications_events(store=store)
    frames: list[bytes] = []

    async def collect() -> None:
        async for chunk in resp.body_iterator:  # type: ignore[attr-defined]
            frames.append(chunk)

    task = asyncio.create_task(collect())
    await asyncio.sleep(0.05)  # let the stream subscribe

    store.publish(routed.id, _user_request("r_routed"))
    store.publish(other.id, _user_request("r_plain"))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass

    joined = b"".join(frames).decode()
    assert "r_plain" in joined
    assert "r_routed" not in joined


async def test_notifications_pending_skips_telegram_routed(
    tmp_path: Path, monkeypatch: Any
) -> None:
    _isolate_bindings(monkeypatch, tmp_path)
    store = SessionStore(db_path=tmp_path / "sessions.sqlite")
    bindings = TelegramBindingStore(tmp_path / "sessions.sqlite")

    routed = store.create()
    other = store.create()
    bindings.upsert(
        chat_id=1, thread_id=0, kind="dm",
        project_id=None, active_session_id=routed.id,
    )
    # The route reads the broker's live-request snapshots — normally
    # populated by broker.ask(); seed them directly for determinism.
    from loom.hitl.broker import HitlRequest

    for sid, rid in ((routed.id, "pr_routed"), (other.id, "pr_plain")):
        store.broker._requests[(sid, rid)] = HitlRequest(  # type: ignore[index]
            session_id=sid, request_id=rid, prompt=f"prompt {rid}", kind="confirm"
        )

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(ask_user_handler=SimpleNamespace(_form_extras={})))
    )
    out = await notifications_pending(request=request, store=store)
    sids = [i["session_id"] for i in out["pending"]]
    assert other.id in sids
    assert routed.id not in sids


async def test_web_push_skipped_for_telegram_routed(
    tmp_path: Path, monkeypatch: Any
) -> None:
    _isolate_bindings(monkeypatch, tmp_path)
    store = SessionStore(db_path=tmp_path / "sessions.sqlite")
    bindings = TelegramBindingStore(tmp_path / "sessions.sqlite")

    routed = store.create()
    other = store.create()
    bindings.upsert(
        chat_id=1, thread_id=0, kind="dm",
        project_id=None, active_session_id=routed.id,
    )

    pushed: list[str] = []
    store._schedule_push = lambda sid, data: pushed.append(sid)  # type: ignore[method-assign]

    store.publish(routed.id, _user_request("pu_routed"))
    store.publish(other.id, _user_request("pu_plain"))

    assert pushed == [other.id]

    # Bell history keeps BOTH (audit trail).
    rows = store.list_hitl_events(limit=10)
    prompts = [r["prompt"] for r in rows]
    assert "prompt pu_routed" in prompts and "prompt pu_plain" in prompts
