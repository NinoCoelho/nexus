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
import json
import logging
from collections.abc import AsyncIterator
from threading import Lock
from typing import Any

from loom.hitl import HitlBroker

from ..events import SessionEvent

log = logging.getLogger(__name__)

# Event kinds that fan out to the global notifications channel in addition
# to per-session subscribers. Kept narrow so non-HITL traffic (delta /
# tool_call / tool_result / iter / reply) stays scoped to the session.
# ``calendar_alert`` is a fire-and-forget notification (no answer expected)
# the calendar heartbeat publishes when a non-prompt event fires.
_GLOBAL_HITL_KINDS = frozenset({
    "user_request",
    "user_request_auto",
    "user_request_cancelled",
    "calendar_alert",
})


class PubSubMixin:
    """Mixin providing publish/subscribe and HITL pending-future management.

    Concrete class must call ``_init_pubsub()`` from its ``__init__``.
    """

    def _init_pubsub(self) -> None:
        self._lock: Lock = Lock()
        self._subscribers: dict[str, list[asyncio.Queue[SessionEvent | None]]] = {}
        # Subscribers to the cross-session HITL notifications channel.
        # Each queue receives ``(session_id, event)`` tuples for whitelisted
        # event kinds (see _GLOBAL_HITL_KINDS).
        self._global_subscribers: list[
            asyncio.Queue[tuple[str, SessionEvent] | None]
        ] = []
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
            global_subscribers = (
                list(self._global_subscribers)
                if event.kind in _GLOBAL_HITL_KINDS
                else ()
            )
        for q in subscribers:
            q.put_nowait(event)
        for gq in global_subscribers:
            gq.put_nowait((session_id, event))
        if event.kind in _GLOBAL_HITL_KINDS:
            self._on_global_hitl_event(session_id, event)

    # ── HITL event log + push fan-out ────────────────────────────────────────
    #
    # Anything in _GLOBAL_HITL_KINDS flows through this hook so we can:
    #  1. write/update a row in ``hitl_events`` for the bell history
    #  2. fan a Web Push notification out to subscribed browsers when a
    #     ``user_request`` arrives (so users see prompts even with no tab
    #     open).
    # All work here is best-effort — exceptions never propagate back to
    # the publishing call site.

    def _on_global_hitl_event(self, session_id: str, event: SessionEvent) -> None:
        try:
            if event.kind == "user_request":
                self._record_hitl_pending(session_id, event.data)
                self._schedule_push(session_id, event.data)
            elif event.kind == "user_request_auto":
                self._record_hitl_auto(session_id, event.data)
            elif event.kind == "user_request_cancelled":
                reason = event.data.get("reason") or "cancelled"
                status = "timed_out" if reason == "timeout" else "cancelled"
                self._mark_hitl_resolved(
                    event.data.get("request_id"), status=status, reason=reason,
                )
        except Exception:  # noqa: BLE001 — best-effort sink
            log.exception("hitl event hook failed for kind=%s", event.kind)

    def _record_hitl_pending(self, session_id: str, data: dict[str, Any]) -> None:
        rid = data.get("request_id")
        if not rid:
            return
        prompt = data.get("prompt") or ""
        kind = data.get("kind") or "confirm"
        payload = {
            "choices": data.get("choices"),
            "default": data.get("default"),
            "timeout_seconds": data.get("timeout_seconds"),
            "fields": data.get("fields"),
            "form_title": data.get("form_title"),
            "form_description": data.get("form_description"),
        }
        self._hitl_db().execute(
            "INSERT OR REPLACE INTO hitl_events "
            "(request_id, session_id, kind, prompt, payload_json, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            (rid, session_id, kind, prompt, json.dumps(payload, ensure_ascii=False)),
        )
        self._hitl_db().commit()

    def _record_hitl_auto(self, session_id: str, data: dict[str, Any]) -> None:
        # YOLO-style auto-answers don't go through _register so they have
        # no pre-existing row. Synthesize a request_id for the log row.
        rid = data.get("request_id") or f"auto-{session_id}-{id(data)}"
        prompt = data.get("prompt") or ""
        kind = data.get("kind") or "confirm"
        answer = data.get("answer")
        self._hitl_db().execute(
            "INSERT OR REPLACE INTO hitl_events "
            "(request_id, session_id, kind, prompt, payload_json, status, "
            " answer, reason, resolved_at) "
            "VALUES (?, ?, ?, ?, ?, 'auto_answered', ?, ?, CURRENT_TIMESTAMP)",
            (
                rid, session_id, kind, prompt, json.dumps({}),
                answer if isinstance(answer, str) else json.dumps(answer),
                data.get("reason"),
            ),
        )
        self._hitl_db().commit()

    def _mark_hitl_resolved(
        self,
        request_id: str | None,
        *,
        status: str,
        reason: str | None = None,
        answer: str | None = None,
    ) -> None:
        if not request_id:
            return
        self._hitl_db().execute(
            "UPDATE hitl_events SET status=?, reason=?, answer=COALESCE(?, answer), "
            "resolved_at=CURRENT_TIMESTAMP "
            "WHERE request_id=? AND status='pending'",
            (status, reason, answer, request_id),
        )
        self._hitl_db().commit()

    # ── HITL history + push subs read/write helpers (used by routes) ─────────

    def _hitl_db(self):
        # Single connection, owned by loom. SQLite isn't thread-safe across
        # connections without WAL + check_same_thread=False — keep all
        # access on the asyncio loop thread.
        return self._loom._db  # type: ignore[attr-defined]

    def list_hitl_events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._hitl_db().execute(
            "SELECT request_id, session_id, kind, prompt, payload_json, "
            "       status, answer, reason, created_at, resolved_at "
            "FROM hitl_events ORDER BY created_at DESC, ROWID DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                payload = json.loads(r[4]) if r[4] else {}
            except (TypeError, json.JSONDecodeError):
                payload = {}
            out.append({
                "request_id": r[0],
                "session_id": r[1],
                "kind": r[2],
                "prompt": r[3],
                "choices": payload.get("choices"),
                "default": payload.get("default"),
                "timeout_seconds": payload.get("timeout_seconds"),
                "fields": payload.get("fields"),
                "form_title": payload.get("form_title"),
                "form_description": payload.get("form_description"),
                "status": r[5],
                "answer": r[6],
                "reason": r[7],
                "created_at": r[8],
                "resolved_at": r[9],
            })
        return out

    def trim_hitl_events(self, *, keep_last_n: int = 200, keep_days: int = 7) -> int:
        """Drop history rows older than ``keep_days`` while keeping the most
        recent ``keep_last_n`` regardless of age. Returns count removed."""
        cur = self._hitl_db().execute(
            "DELETE FROM hitl_events WHERE request_id NOT IN ("
            "  SELECT request_id FROM hitl_events ORDER BY created_at DESC LIMIT ?"
            ") AND datetime(created_at) < datetime('now', ?)",
            (max(1, int(keep_last_n)), f"-{int(keep_days)} days"),
        )
        removed = cur.rowcount or 0
        self._hitl_db().commit()
        return removed

    def list_push_subscriptions(self) -> list[dict[str, Any]]:
        rows = self._hitl_db().execute(
            "SELECT endpoint, p256dh, auth, user_agent FROM push_subscriptions"
        ).fetchall()
        return [
            {"endpoint": r[0], "p256dh": r[1], "auth": r[2], "user_agent": r[3]}
            for r in rows
        ]

    def upsert_push_subscription(
        self, *, endpoint: str, p256dh: str, auth: str, user_agent: str | None = None,
    ) -> None:
        self._hitl_db().execute(
            "INSERT INTO push_subscriptions (endpoint, p256dh, auth, user_agent) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(endpoint) DO UPDATE SET "
            "  p256dh=excluded.p256dh, auth=excluded.auth, "
            "  user_agent=COALESCE(excluded.user_agent, user_agent), "
            "  last_seen_at=CURRENT_TIMESTAMP",
            (endpoint, p256dh, auth, user_agent),
        )
        self._hitl_db().commit()

    def delete_push_subscription(self, endpoint: str) -> bool:
        cur = self._hitl_db().execute(
            "DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,)
        )
        self._hitl_db().commit()
        return (cur.rowcount or 0) > 0

    def _schedule_push(self, session_id: str, data: dict[str, Any]) -> None:
        try:
            from ...push import sender as push_sender
        except Exception:  # noqa: BLE001
            return
        if not push_sender.is_configured():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # publish called outside a loop (test/sync context)
        loop.create_task(
            push_sender.fan_out(
                store=self,
                session_id=session_id,
                request_data=data,
            )
        )

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

    async def subscribe_global(
        self,
    ) -> AsyncIterator[tuple[str, SessionEvent]]:
        """Subscribe to the cross-session HITL notifications channel.

        Yields ``(session_id, event)`` tuples whenever any session
        publishes an event whose kind is in ``_GLOBAL_HITL_KINDS``.
        Used by ``GET /notifications/events`` so the UI can surface a
        single popup regardless of which session is currently active.
        """
        queue: asyncio.Queue[tuple[str, SessionEvent] | None] = asyncio.Queue()
        with self._lock:
            self._global_subscribers.append(queue)
        try:
            while True:
                item = await queue.get()
                if item is None:
                    return
                yield item
        finally:
            with self._lock:
                if queue in self._global_subscribers:
                    self._global_subscribers.remove(queue)

    # ── HITL pending futures ─────────────────────────────────────────────────

    def register_pending(self, session_id: str, request_id: str) -> asyncio.Future[str]:
        try:
            return self._broker._register(session_id, request_id)
        except ValueError as exc:
            raise ValueError(f"request_id already pending: {request_id!r}") from exc

    def resolve_pending(self, session_id: str, request_id: str, answer: str) -> bool:
        ok = self._broker.resolve(session_id, request_id, answer)
        if ok:
            try:
                self._mark_hitl_resolved(
                    request_id, status="answered", answer=answer,
                )
            except Exception:  # noqa: BLE001
                log.exception("hitl history update on resolve failed")
        return ok

    def cancel_pending(self, session_id: str, request_id: str) -> bool:
        fut = self._broker._pending.pop((session_id, request_id), None)
        self._broker._requests.pop((session_id, request_id), None)
        if fut is None or fut.done():
            return False
        fut.cancel()
        return True

    def _cancel_all_pending(self, session_id: str) -> None:
        self._broker.cancel_session(session_id, reason="session_reset")
