"""In-memory pub/sub bus for the out-of-band SSE event channel.

Used by ``GET /chat/{sid}/events`` to push HITL and lifecycle events
to connected browser clients without coupling to the HTTP request cycle.

Exports ``PubSubMixin`` — a mixin that adds ``publish`` / ``subscribe``
and the ``_subscribers`` registry to the ``SessionStore`` class.

The ``HitlBroker`` integration (``register_pending`` / ``resolve_pending`` /
``cancel_pending``) is also housed here because it shares the same
in-memory state lifetime as the subscriber dict.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from threading import Lock

from loom.hitl import HitlBroker

from ..events import SessionEvent


class PubSubMixin:
    """Mixin providing publish/subscribe and HITL pending-future management.

    Concrete class must call ``_init_pubsub()`` from its ``__init__``.
    """

    def _init_pubsub(self) -> None:
        self._lock: Lock = Lock()
        self._subscribers: dict[str, list[asyncio.Queue[SessionEvent | None]]] = {}
        self._broker = HitlBroker(
            publish_hook=lambda sid, ev: self.publish(
                sid, SessionEvent(kind=ev.kind, data=dict(ev.data))
            )
        )

    @property
    def broker(self) -> HitlBroker:
        return self._broker

    # ── pub/sub for SSE ──────────────────────────────────────────────────────

    def publish(self, session_id: str, event: SessionEvent) -> None:
        with self._lock:
            subscribers = list(self._subscribers.get(session_id, ()))
        for q in subscribers:
            q.put_nowait(event)

    async def subscribe(self, session_id: str) -> AsyncIterator[SessionEvent]:
        queue: asyncio.Queue[SessionEvent | None] = asyncio.Queue()
        with self._lock:
            self._subscribers.setdefault(session_id, []).append(queue)
        try:
            while True:
                event = await queue.get()
                if event is None:
                    return
                yield event
        finally:
            with self._lock:
                subs = self._subscribers.get(session_id)
                if subs is not None and queue in subs:
                    subs.remove(queue)
                    if not subs:
                        self._subscribers.pop(session_id, None)

    # ── HITL pending futures ─────────────────────────────────────────────────

    def register_pending(self, session_id: str, request_id: str) -> asyncio.Future[str]:
        try:
            return self._broker._register(session_id, request_id)
        except ValueError as exc:
            raise ValueError(f"request_id already pending: {request_id!r}") from exc

    def resolve_pending(self, session_id: str, request_id: str, answer: str) -> bool:
        return self._broker.resolve(session_id, request_id, answer)

    def cancel_pending(self, session_id: str, request_id: str) -> bool:
        fut = self._broker._pending.pop((session_id, request_id), None)
        self._broker._requests.pop((session_id, request_id), None)
        if fut is None or fut.done():
            return False
        fut.cancel()
        return True

    def _cancel_all_pending(self, session_id: str) -> None:
        self._broker.cancel_session(session_id, reason="session_reset")
