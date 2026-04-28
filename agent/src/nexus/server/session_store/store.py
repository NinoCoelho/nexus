"""SessionStore — Nexus-side composition layer over ``loom.store.session.SessionStore``.

Loom owns all persistence (sessions + messages tables). Nexus adds:

* FTS5 full-text search with BM25 ranking and snippet highlighting
  (loom's built-in search uses LIKE — insufficient for the UI's search UX).
* The HITL event pub/sub bus (``publish`` / ``subscribe``) for the out-of-band
  SSE channel at ``GET /chat/{sid}/events``.
* Pending-future registry (``register_pending`` / ``resolve_pending`` /
  ``cancel_pending``) used by ``ask_user`` to park until the UI responds.
* A one-shot migration from the pre-loom Nexus schema (integer timestamps,
  no WAL, no ``pending_question`` column) run at first init. The old file is
  backed up as ``sessions.sqlite.pre-loom-migration.bak`` before any write.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from loom.store.session import SessionStore as LoomSessionStore

from ...agent.llm import ChatMessage, Role, ToolCall
from .models import Session, SessionSummary, _from_loom_msg, _to_loom_msg, _ts_to_int
from .pubsub import PubSubMixin
from .query import QueryMixin
from .schema import init_fts, migrate_legacy_schema

log = logging.getLogger(__name__)

_DB_PATH = Path("~/.nexus/sessions.sqlite").expanduser()


class SessionStore(PubSubMixin, QueryMixin):
    """Nexus's session store — persists via loom, adds HITL + FTS5 search.

    All storage-level operations delegate to ``self._loom``
    (a ``loom.store.session.SessionStore``). Nexus owns only:

    - FTS5 virtual table + triggers for snippet-rich full-text search.
    - HITL pub/sub bus (``publish`` / ``subscribe`` / ``register_pending``
      / ``resolve_pending`` / ``cancel_pending``).
    - The ``Session`` + ``SessionSummary`` dataclasses used by FastAPI handlers.
    - A one-shot migration from the pre-loom integer-timestamp schema.
    """

    def __init__(self, db_path: Path = _DB_PATH) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        # One-shot migration from the pre-loom schema — runs before loom
        # touches the file so the backup is pristine.
        if self._db_path.exists():
            migrate_legacy_schema(self._db_path)

        # Loom handles all schema creation and persistence.
        self._loom = LoomSessionStore(self._db_path)

        # Add FTS5 on top of loom's messages table so search has snippets.
        init_fts(self._loom)

        # Initialise pub/sub + HITL broker (from PubSubMixin).
        self._init_pubsub()

    def _connect(self) -> sqlite3.Connection:
        """Open a read-only-ish connection for ad-hoc queries (e.g. InsightsEngine)."""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ── persistence — delegate to loom ───────────────────────────────────────

    def create(self, context: str | None = None) -> Session:
        sid = uuid.uuid4().hex
        d = self._loom.get_or_create(sid, title="New session", context=context)
        return Session(id=d["id"], title=d["title"] or "New session", context=context)

    def create_child(
        self,
        *,
        parent_session_id: str,
        title: str | None = None,
        hidden: bool = True,
    ) -> Session:
        """Create a new session linked to ``parent_session_id``.

        Used by the spawn_subagents tool: the child runs a fresh agent loop
        in isolation and its transcript is persisted under a real session
        id. Hidden child sessions are excluded from ``list()`` by default.
        """
        sid = uuid.uuid4().hex
        d = self._loom.get_or_create(sid, title=title or "Sub-agent", context=None)
        self._loom._db.execute(
            "UPDATE sessions SET parent_session_id = ?, hidden = ? WHERE id = ?",
            (parent_session_id, 1 if hidden else 0, sid),
        )
        self._loom._db.commit()
        return Session(id=d["id"], title=d["title"] or "Sub-agent", context=None)

    def get_or_create(self, session_id: str | None, context: str | None = None) -> Session:
        if session_id is None:
            return self.create(context=context)
        d = self._loom.get_or_create(session_id, title="New session", context=context)
        # If context wasn't set but we have one now, propagate it.
        if d.get("context") is None and context is not None:
            self._loom.set_context(session_id, context)
            d["context"] = context
        history = [_from_loom_msg(m) for m in self._loom.get_history(session_id)]
        return Session(
            id=d["id"],
            title=d["title"] or "New session",
            history=history,
            context=d.get("context") or context,
        )

    def get(self, session_id: str) -> Session | None:
        # Use raw DB to avoid creating the session if it doesn't exist.
        row = self._loom._db.execute(
            "SELECT id, title, context FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        msg_rows = self._loom._db.execute(
            "SELECT role, content, tool_calls, tool_call_id, name, created_at "
            "FROM messages WHERE session_id = ? ORDER BY seq",
            (session_id,),
        ).fetchall()
        history: list[ChatMessage] = []
        timestamps: list[int] = []
        for r in msg_rows:
            import loom.types as lt
            loom_tcs: list[lt.ToolCall] | None = None
            if r[2]:
                try:
                    raw = json.loads(r[2])
                    loom_tcs = [lt.ToolCall(**tc) for tc in raw]
                except Exception:
                    pass
            loom_msg = lt.ChatMessage(
                role=lt.Role(r[0]),
                content=r[1],
                tool_calls=loom_tcs,
                tool_call_id=r[3],
                name=r[4],
            )
            history.append(_from_loom_msg(loom_msg))
            timestamps.append(_ts_to_int(r[5]))
        sess = Session(id=row[0], title=row[1] or "New session", history=history, context=row[2])
        sess._message_timestamps = timestamps  # type: ignore[attr-defined]
        return sess

    def list(self, limit: int = 50, *, include_hidden: bool = False) -> list[SessionSummary]:
        # ``hidden`` exists from the spawn_subagents migration; filter out
        # child sessions by default so the sidebar doesn't surface them.
        where = "" if include_hidden else "WHERE COALESCE(s.hidden, 0) = 0"
        rows = self._loom._db.execute(
            f"""
            SELECT s.id, s.title, s.created_at, s.updated_at,
                   COUNT(m.seq) AS message_count
            FROM sessions s
            LEFT JOIN messages m ON m.session_id = s.id
            {where}
            GROUP BY s.id
            ORDER BY s.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            SessionSummary(
                id=r[0],
                title=r[1] or "New session",
                created_at=_ts_to_int(r[2]),
                updated_at=_ts_to_int(r[3]),
                message_count=r[4] or 0,
            )
            for r in rows
        ]

    def persist_partial_turn(
        self,
        session_id: str,
        *,
        base_history: list[ChatMessage],
        user_message: str,
        assistant_text: str,
        tool_calls: list[dict[str, Any]] | None = None,
        status_note: str | None = None,
    ) -> None:
        """Persist the current turn state when the stream didn't reach ``done``.

        Writes ``base_history`` + user + a best-effort assistant message (content
        prefixed with an ``[interrupted: reason]`` note so the UI can render it
        as partial). Synthesised ``ToolCall`` entries from the live tool events
        go on the assistant message so badges survive reload.

        No-op if there's nothing to persist.
        """
        if not user_message and not assistant_text and not tool_calls:
            return
        tcs: list[ToolCall] = []
        for t in tool_calls or []:
            name = t.get("name") or ""
            if not name:
                continue
            args = t.get("args")
            if isinstance(args, str):
                try:
                    args_dict = json.loads(args) if args else {}
                    if not isinstance(args_dict, dict):
                        args_dict = {"_raw": args}
                except (TypeError, json.JSONDecodeError):
                    args_dict = {"_raw": args}
            elif isinstance(args, dict):
                args_dict = args
            else:
                args_dict = {}
            tcs.append(ToolCall(id=t.get("id") or f"partial-{len(tcs)}", name=name, arguments=args_dict))
        prefix = f"[{status_note}] " if status_note else ""
        assistant = ChatMessage(
            role=Role.ASSISTANT,
            content=(prefix + assistant_text) if (prefix or assistant_text) else "",
            tool_calls=tcs,
        )
        history = list(base_history)
        history.append(ChatMessage(role=Role.USER, content=user_message))
        history.append(assistant)
        self.replace_history(session_id, history)

    def replace_history(self, session_id: str, history: list[ChatMessage]) -> None:
        loom_msgs = [_to_loom_msg(m) for m in history]
        self._loom.replace_history(session_id, loom_msgs)

        # Auto-title: if the session is still "New session", set title from
        # the first user message (loom's replace_history doesn't do this).
        row = self._loom._db.execute(
            "SELECT title FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row and (row[0] is None or row[0] == "New session"):
            for msg in history:
                if msg.role == Role.USER and msg.content:
                    title = msg.content.strip()[:40]
                    self._loom.set_title(session_id, title)
                    break

    def reset(self, session_id: str) -> None:
        self._loom.reset(session_id)
        self._cancel_all_pending(session_id)

    def delete(self, session_id: str) -> None:
        self._loom.delete_session(session_id)
        self._loom._db.execute(
            "DELETE FROM message_feedback WHERE session_id = ?", (session_id,)
        )
        self._loom._db.commit()
        self._cancel_all_pending(session_id)

    # ── feedback ─────────────────────────────────────────────────────────────

    def set_feedback(self, session_id: str, seq: int, value: str | None) -> None:
        """Set or clear thumbs feedback for a message in a session.

        ``value`` must be ``"up"``, ``"down"``, or ``None`` (clears the rating
        but preserves any pin on the same row).
        """
        if value is not None and value not in ("up", "down"):
            raise ValueError(f"invalid feedback value: {value!r}")
        self._loom._db.execute(
            "INSERT INTO message_feedback (session_id, seq, value) VALUES (?, ?, ?) "
            "ON CONFLICT(session_id, seq) DO UPDATE SET value = excluded.value, "
            "created_at = CURRENT_TIMESTAMP",
            (session_id, seq, value),
        )
        # Drop fully-empty rows (no rating, no pin) to keep the table tidy.
        self._loom._db.execute(
            "DELETE FROM message_feedback "
            "WHERE session_id = ? AND seq = ? AND value IS NULL AND pinned = 0",
            (session_id, seq),
        )
        self._loom._db.commit()

    def set_pinned(self, session_id: str, seq: int, pinned: bool) -> None:
        """Set or clear the pinned flag for a message in a session."""
        self._loom._db.execute(
            "INSERT INTO message_feedback (session_id, seq, pinned) VALUES (?, ?, ?) "
            "ON CONFLICT(session_id, seq) DO UPDATE SET pinned = excluded.pinned",
            (session_id, seq, 1 if pinned else 0),
        )
        self._loom._db.execute(
            "DELETE FROM message_feedback "
            "WHERE session_id = ? AND seq = ? AND value IS NULL AND pinned = 0",
            (session_id, seq),
        )
        self._loom._db.commit()

    def get_feedback_map(self, session_id: str) -> dict[int, str]:
        """Return ``{seq: value}`` for all feedback rows of a session."""
        rows = self._loom._db.execute(
            "SELECT seq, value FROM message_feedback "
            "WHERE session_id = ? AND value IS NOT NULL",
            (session_id,),
        ).fetchall()
        return {int(r[0]): str(r[1]) for r in rows}

    def get_pinned_set(self, session_id: str) -> set[int]:
        """Return the set of seq numbers that are pinned in this session."""
        rows = self._loom._db.execute(
            "SELECT seq FROM message_feedback WHERE session_id = ? AND pinned = 1",
            (session_id,),
        ).fetchall()
        return {int(r[0]) for r in rows}

    def list_pinned_across_sessions(self, limit: int = 50) -> list[dict]:
        """Return recent pinned messages across all sessions, newest first."""
        rows = self._loom._db.execute(
            """
            SELECT mf.session_id, mf.seq, m.role, m.content, m.created_at,
                   s.title
            FROM message_feedback mf
            JOIN messages m ON m.session_id = mf.session_id AND m.seq = mf.seq
            LEFT JOIN sessions s ON s.id = mf.session_id
            WHERE mf.pinned = 1
            ORDER BY datetime(m.created_at) DESC
            LIMIT ?
            """,
            (max(1, min(limit, 500)),),
        ).fetchall()
        return [
            {
                "session_id": r[0],
                "seq": int(r[1]),
                "role": r[2],
                "content": r[3] or "",
                "created_at": r[4],
                "session_title": r[5] or "New session",
            }
            for r in rows
        ]

    def rename(self, session_id: str, title: str) -> None:
        self._loom.set_title(session_id, title)
        # Also bump updated_at.
        self._loom._db.execute(
            "UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (session_id,),
        )
        self._loom._db.commit()

    def bump_usage(
        self,
        session_id: str,
        *,
        model: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        tool_calls: int = 0,
    ) -> None:
        self._loom.bump_usage(session_id, input_tokens, output_tokens, tool_calls)
        if model:
            self._loom._db.execute(
                "UPDATE sessions SET model = ? WHERE id = ? AND (model IS NULL OR model = '')",
                (model, session_id),
            )
            self._loom._db.commit()
