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
    "calendar_alarm",
    "voice_ack",
    "nexus_tier_changed",
})


def _row_to_pending_dict(r: Any) -> dict[str, Any]:
    """Hydrate a hitl_pending row tuple into the dict shape used by routes."""
    def _load(s: Any) -> Any:
        if not s:
            return None
        try:
            return json.loads(s)
        except (TypeError, json.JSONDecodeError):
            return None

    return {
        "request_id": r[0],
        "session_id": r[1],
        "tool_call_id": r[2],
        "kind": r[3],
        "prompt": r[4],
        "choices": _load(r[5]),
        "fields": _load(r[6]),
        "form_title": r[7],
        "form_description": r[8],
        "default": r[9],
        "timeout_seconds": r[10],
        "deadline_at": r[11],
        "created_at": r[12],
        "parked_messages_json": r[13] or "[]",
        "model_id": r[14],
        "status": r[15],
        "answered_at": r[16],
        "answer_json": r[17],
    }


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
        # In-flight tool_call_id keyed by session_id, updated by the agent
        # façade on each tool_exec_start. ask_user_tool reads this when it
        # parks a request so we can persist tool_call_id alongside the
        # parked snapshot. Sequential within a turn; no lock needed.
        self._latest_tool_call_id: dict[str, str] = {}
        # Last assembled loom messages snapshot keyed by session_id, kept
        # by the agent façade so ask_user_tool can persist a recoverable
        # parked state from any tool dispatch point in the turn.
        self._latest_messages_snapshot: dict[str, list[dict[str, Any]]] = {}
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
        # LEFT JOIN sessions for the title (so the bell can show "parked
        # in <chat>") and hitl_pending to flag rows that landed durably
        # — durable = "parked", in-memory = "live", they need different
        # cancel paths and different urgency cues.
        rows = self._hitl_db().execute(
            "SELECT e.request_id, e.session_id, e.kind, e.prompt, "
            "       e.payload_json, e.status, e.answer, e.reason, "
            "       e.created_at, e.resolved_at, "
            "       s.title, "
            "       CASE WHEN p.status = 'parked' THEN 1 ELSE 0 END "
            "  FROM hitl_events e "
            "  LEFT JOIN sessions s ON s.id = e.session_id "
            "  LEFT JOIN hitl_pending p ON p.request_id = e.request_id "
            " ORDER BY e.created_at DESC, e.ROWID DESC LIMIT ?",
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
                "session_title": r[10],
                "parked": bool(r[11]),
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

    # ── Durable parked HITL state (survives restart) ─────────────────────────

    def persist_hitl_pending(
        self,
        *,
        session_id: str,
        request_id: str,
        tool_call_id: str,
        kind: str,
        prompt: str,
        choices: list[str] | None,
        fields: list[dict[str, Any]] | None,
        form_title: str | None,
        form_description: str | None,
        default: str | None,
        timeout_seconds: int | None,
        deadline_at: str | None = None,
        parked_messages_json: str | None = None,
        model_id: str | None = None,
    ) -> None:
        """Insert (or refresh) a parked HITL request row.

        Idempotent on ``request_id`` so retries during park don't duplicate.
        ``parked_messages_json`` may be empty initially — the agent façade
        backfills it once the working snapshot is known.
        """
        self._hitl_db().execute(
            "INSERT OR REPLACE INTO hitl_pending "
            "(request_id, session_id, tool_call_id, kind, prompt, "
            " choices_json, fields_json, form_title, form_description, "
            " \"default\", timeout_seconds, deadline_at, "
            " parked_messages_json, model_id, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'parked')",
            (
                request_id,
                session_id,
                tool_call_id,
                kind,
                prompt,
                json.dumps(choices) if choices is not None else None,
                json.dumps(fields) if fields is not None else None,
                form_title,
                form_description,
                default,
                int(timeout_seconds) if timeout_seconds is not None else None,
                deadline_at,
                parked_messages_json or "[]",
                model_id,
            ),
        )
        self._hitl_db().commit()

    def update_hitl_pending_snapshot(
        self,
        request_id: str,
        parked_messages_json: str,
        *,
        model_id: str | None = None,
    ) -> bool:
        """Backfill the snapshot once the agent façade has assembled it.

        Returns False if the row is missing or already resolved.
        """
        cur = self._hitl_db().execute(
            "UPDATE hitl_pending SET parked_messages_json = ?, "
            "       model_id = COALESCE(?, model_id) "
            "WHERE request_id = ? AND status = 'parked'",
            (parked_messages_json, model_id, request_id),
        )
        self._hitl_db().commit()
        return (cur.rowcount or 0) > 0

    def get_hitl_pending(self, request_id: str) -> dict[str, Any] | None:
        row = self._hitl_db().execute(
            "SELECT request_id, session_id, tool_call_id, kind, prompt, "
            "       choices_json, fields_json, form_title, form_description, "
            "       \"default\", timeout_seconds, deadline_at, created_at, "
            "       parked_messages_json, model_id, status, "
            "       answered_at, answer_json "
            "FROM hitl_pending WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_pending_dict(row)

    def list_pending_for_session(
        self, session_id: str, *, kind: str | None = None,
    ) -> list[dict[str, Any]]:
        if kind is not None:
            rows = self._hitl_db().execute(
                "SELECT request_id, session_id, tool_call_id, kind, prompt, "
                "       choices_json, fields_json, form_title, form_description, "
                "       \"default\", timeout_seconds, deadline_at, created_at, "
                "       parked_messages_json, model_id, status, "
                "       answered_at, answer_json "
                "FROM hitl_pending "
                "WHERE session_id = ? AND status = 'parked' AND kind = ? "
                "ORDER BY created_at ASC",
                (session_id, kind),
            ).fetchall()
        else:
            rows = self._hitl_db().execute(
                "SELECT request_id, session_id, tool_call_id, kind, prompt, "
                "       choices_json, fields_json, form_title, form_description, "
                "       \"default\", timeout_seconds, deadline_at, created_at, "
                "       parked_messages_json, model_id, status, "
                "       answered_at, answer_json "
                "FROM hitl_pending "
                "WHERE session_id = ? AND status = 'parked' "
                "ORDER BY created_at ASC",
                (session_id,),
            ).fetchall()
        return [_row_to_pending_dict(r) for r in rows]

    def list_all_pending(self) -> list[dict[str, Any]]:
        rows = self._hitl_db().execute(
            "SELECT request_id, session_id, tool_call_id, kind, prompt, "
            "       choices_json, fields_json, form_title, form_description, "
            "       \"default\", timeout_seconds, deadline_at, created_at, "
            "       parked_messages_json, model_id, status, "
            "       answered_at, answer_json "
            "FROM hitl_pending WHERE status = 'parked' "
            "ORDER BY created_at ASC"
        ).fetchall()
        return [_row_to_pending_dict(r) for r in rows]

    def mark_hitl_pending_answered(
        self, request_id: str, answer: Any,
    ) -> dict[str, Any] | None:
        """Atomically resolve a parked request.

        Returns the row (with ``already_answered`` set) when a row exists.
        Returns None if no such request_id was ever parked.
        """
        try:
            answer_json = json.dumps(answer, ensure_ascii=False)
        except (TypeError, ValueError):
            answer_json = json.dumps(str(answer))
        cur = self._hitl_db().execute(
            "UPDATE hitl_pending "
            "SET status = 'answered', answered_at = CURRENT_TIMESTAMP, "
            "    answer_json = ? "
            "WHERE request_id = ? AND status = 'parked'",
            (answer_json, request_id),
        )
        self._hitl_db().commit()
        won = (cur.rowcount or 0) > 0
        # Mirror to hitl_events so the bell history reflects the resolution.
        # Without this the row stays 'pending' in the bell forever even
        # though hitl_pending shows it answered.
        if won:
            try:
                answer_text = (
                    answer if isinstance(answer, str) else answer_json
                )
                self._mark_hitl_resolved(
                    request_id, status="answered", answer=answer_text,
                )
            except Exception:  # noqa: BLE001
                log.exception("hitl history update on park-answer failed")
        row = self.get_hitl_pending(request_id)
        if row is None:
            return None
        row["already_answered"] = not won
        return row

    def cancel_hitl_pending(
        self, request_id: str, *, reason: str = "cancelled",
    ) -> bool:
        new_status = "expired" if reason == "expired" else "cancelled"
        cur = self._hitl_db().execute(
            "UPDATE hitl_pending SET status = ?, "
            "       answered_at = CURRENT_TIMESTAMP "
            "WHERE request_id = ? AND status = 'parked'",
            (new_status, request_id),
        )
        self._hitl_db().commit()
        won = (cur.rowcount or 0) > 0
        # Mirror to hitl_events. Otherwise a superseded / session_reset /
        # expired parked row would linger as 'pending' in the bell.
        if won:
            try:
                self._mark_hitl_resolved(
                    request_id, status=new_status, reason=reason,
                )
            except Exception:  # noqa: BLE001
                log.exception("hitl history update on park-cancel failed")
        return won

    def trim_hitl_pending(self, *, keep_days: int = 30) -> int:
        cur = self._hitl_db().execute(
            "DELETE FROM hitl_pending "
            "WHERE status != 'parked' "
            "  AND datetime(COALESCE(answered_at, created_at)) "
            "      < datetime('now', ?)",
            (f"-{int(keep_days)} days",),
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
        # Mark any durable parked rows for this session as cancelled too.
        try:
            for row in self.list_pending_for_session(session_id):
                self.cancel_hitl_pending(row["request_id"], reason="session_reset")
        except Exception:  # noqa: BLE001
            log.exception("cancel parked rows on session reset failed")

    # ── Per-turn dispatch context (set by agent façade) ──────────────────────

    def set_pending_tool_call(self, session_id: str, tool_call_id: str) -> None:
        """Record the tool_call_id loom is about to dispatch for this session.

        Updated on each ``tool_exec_start`` so HITL handlers (ask_user) can
        persist it alongside their parked state. Race-free because tool
        dispatch is sequential within a turn.
        """
        self._latest_tool_call_id[session_id] = tool_call_id

    def get_pending_tool_call(self, session_id: str) -> str | None:
        return self._latest_tool_call_id.get(session_id)

    def clear_pending_tool_call(self, session_id: str) -> None:
        self._latest_tool_call_id.pop(session_id, None)

    def set_messages_snapshot(
        self, session_id: str, messages_json: list[dict[str, Any]],
    ) -> None:
        """Snapshot of loom's all_messages up through the current dispatch.

        The agent façade keeps this updated as it forwards loom events.
        ask_user_tool persists it with the parked row so resume can rebuild
        the conversation exactly where it left off.
        """
        self._latest_messages_snapshot[session_id] = messages_json

    def get_messages_snapshot(
        self, session_id: str,
    ) -> list[dict[str, Any]] | None:
        return self._latest_messages_snapshot.get(session_id)

    def clear_messages_snapshot(self, session_id: str) -> None:
        self._latest_messages_snapshot.pop(session_id, None)
