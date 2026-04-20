"""SQLite-backed session store for Nexus.

Beyond the persisted history, the store carries two in-memory
side-channels used by the HITL (human-in-the-loop) pattern:

* ``_subscribers``: per-session list of ``asyncio.Queue`` objects that
  back SSE subscribers. ``publish`` fans out to all of them with
  ``put_nowait`` so tracing the agent never blocks.
* ``_pending``: per-session map of ``request_id`` → ``asyncio.Future``
  used by ``ask_user`` to park until the UI POSTs a response. Stays in
  memory — a process crash cancels every in-flight dialog, which is
  the right behavior anyway.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

from ..agent.llm import ChatMessage, Role, ToolCall
from .events import SessionEvent


_DB_PATH = Path("~/.nexus/sessions.sqlite").expanduser()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    context TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    model TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    tool_call_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_calls TEXT,
    tool_call_id TEXT,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (session_id, seq),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    content='messages',
    content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content)
        VALUES ('delete', old.rowid, old.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content)
        VALUES ('delete', old.rowid, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END;
"""

# Columns added after the initial schema shipped. SQLite's CREATE TABLE
# IF NOT EXISTS won't add them to a pre-existing table, so we run best-
# effort ALTER TABLEs at init. Each entry is (column_name, column_def).
_MIGRATIONS: list[tuple[str, str]] = [
    ("model", "TEXT"),
    ("input_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("output_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("tool_call_count", "INTEGER NOT NULL DEFAULT 0"),
]


@dataclass
class Session:
    id: str
    title: str
    history: list[ChatMessage] = field(default_factory=list)
    context: str | None = None


@dataclass
class SessionSummary:
    id: str
    title: str
    created_at: int
    updated_at: int
    message_count: int


def _row_to_message(row: tuple) -> ChatMessage:
    _seq, role, content, tool_calls_json, tool_call_id = row
    tool_calls: list[ToolCall] = []
    if tool_calls_json:
        raw = json.loads(tool_calls_json)
        tool_calls = [ToolCall(**tc) for tc in raw]
    return ChatMessage(
        role=Role(role),
        content=content or None,
        tool_calls=tool_calls,
        tool_call_id=tool_call_id,
    )


def _message_to_row(seq: int, session_id: str, msg: ChatMessage, ts: int) -> tuple:
    tool_calls_json = None
    if msg.tool_calls:
        tool_calls_json = json.dumps([tc.model_dump() for tc in msg.tool_calls])
    return (session_id, seq, msg.role, msg.content or "", tool_calls_json, msg.tool_call_id, ts)


class SessionStore:
    def __init__(self, db_path: Path = _DB_PATH) -> None:
        self._db_path = db_path
        self._lock = Lock()
        # In-memory side channels — not persisted. Each session that has
        # at least one live SSE subscriber gets a list of queues; each
        # session with at least one pending ``ask_user`` gets a dict of
        # request_id → Future. Both cleaned up lazily.
        self._subscribers: dict[str, list[asyncio.Queue[SessionEvent | None]]] = {}
        self._pending: dict[str, dict[str, asyncio.Future[str]]] = {}
        self._init_db()

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            # Check whether the FTS virtual table already exists *before*
            # running the schema script, so we know if a rebuild is needed.
            fts_existed = bool(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages_fts'"
                ).fetchone()
            )
            conn.executescript(_SCHEMA)
            # Forward-migrate older DBs that predate the usage columns.
            existing = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
            }
            for col, defn in _MIGRATIONS:
                if col not in existing:
                    conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} {defn}")
            # Populate FTS for existing databases that had data before the
            # virtual table was created. The triggers only fire for future
            # writes, so we need a one-time rebuild to backfill historical
            # content. Run it only when the table was just created (i.e. it
            # did not exist before this _init_db call).
            if not fts_existed:
                msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                if msg_count > 0:
                    conn.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")
                    conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def create(self, context: str | None = None) -> Session:
        sid = uuid.uuid4().hex
        now = int(time.time())
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, title, context, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (sid, "New session", context, now, now),
            )
        return Session(id=sid, title="New session", context=context)

    def get_or_create(self, session_id: str | None, context: str | None = None) -> Session:
        if session_id is None:
            return self.create(context=context)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id, title, context FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                now = int(time.time())
                conn.execute(
                    "INSERT INTO sessions (id, title, context, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (session_id, "New session", context, now, now),
                )
                history: list[ChatMessage] = []
                ctx = context
            else:
                ctx = row["context"] or context
                if row["context"] is None and context is not None:
                    conn.execute("UPDATE sessions SET context = ? WHERE id = ?", (context, session_id))
                rows = conn.execute(
                    "SELECT seq, role, content, tool_calls, tool_call_id FROM messages WHERE session_id = ? ORDER BY seq",
                    (session_id,),
                ).fetchall()
                history = [_row_to_message(tuple(r)) for r in rows]
        return Session(id=session_id, title=row["title"] if row else "New session", history=history, context=ctx)

    def get(self, session_id: str) -> Session | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, title, context FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            rows = conn.execute(
                "SELECT seq, role, content, tool_calls, tool_call_id, created_at FROM messages WHERE session_id = ? ORDER BY seq",
                (session_id,),
            ).fetchall()
            history = []
            message_timestamps = []
            for r in rows:
                history.append(_row_to_message((r["seq"], r["role"], r["content"], r["tool_calls"], r["tool_call_id"])))
                message_timestamps.append(r["created_at"])
        sess = Session(id=row["id"], title=row["title"], history=history, context=row["context"])
        # Attach timestamps alongside so the route can emit them without a schema change.
        sess._message_timestamps = message_timestamps  # type: ignore[attr-defined]
        return sess

    def list(self, limit: int = 50) -> list[SessionSummary]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT s.id, s.title, s.created_at, s.updated_at,
                       COUNT(m.seq) AS message_count
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id
                GROUP BY s.id
                ORDER BY s.updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            SessionSummary(
                id=r["id"],
                title=r["title"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                message_count=r["message_count"],
            )
            for r in rows
        ]

    def replace_history(self, session_id: str, history: list[ChatMessage]) -> None:
        now = int(time.time())
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT title FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                return
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            rows = [_message_to_row(i, session_id, msg, now) for i, msg in enumerate(history)]
            conn.executemany(
                "INSERT INTO messages (session_id, seq, role, content, tool_calls, tool_call_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
            # Auto-title on first user message
            if row["title"] == "New session":
                for msg in history:
                    if msg.role == Role.USER and msg.content:
                        title = msg.content.strip()[:40]
                        conn.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, session_id))
                        break

    def reset(self, session_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (int(time.time()), session_id))
        # Cancel any pending HITL futures for this session — they're
        # stale state once the conversation has been reset.
        self._cancel_all_pending(session_id)

    def delete(self, session_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        # Same story as reset: an in-flight ask_user against a deleted
        # session can never resolve; cancel so the tool returns cleanly.
        self._cancel_all_pending(session_id)

    def rename(self, session_id: str, title: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?", (title, int(time.time()), session_id))

    def bump_usage(
        self,
        session_id: str,
        *,
        model: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        tool_calls: int = 0,
    ) -> None:
        """Accumulate usage stats for a session after each LLM turn.

        ``model`` is written only when non-empty so we don't overwrite a
        previously-known model with ``NULL`` on a follow-up turn that
        couldn't resolve the slug. Token + tool counts are additive.
        """
        with self._lock, self._connect() as conn:
            if model:
                conn.execute(
                    "UPDATE sessions SET "
                    "  input_tokens = input_tokens + ?, "
                    "  output_tokens = output_tokens + ?, "
                    "  tool_call_count = tool_call_count + ?, "
                    "  model = ? "
                    "WHERE id = ?",
                    (input_tokens, output_tokens, tool_calls, model, session_id),
                )
            else:
                conn.execute(
                    "UPDATE sessions SET "
                    "  input_tokens = input_tokens + ?, "
                    "  output_tokens = output_tokens + ?, "
                    "  tool_call_count = tool_call_count + ? "
                    "WHERE id = ?",
                    (input_tokens, output_tokens, tool_calls, session_id),
                )

    def search(self, q: str, *, limit: int = 20) -> list[dict]:
        """Full-text search over message content using BM25 ranking.

        Returns a list of ``{"session_id": str, "title": str, "snippet": str}``
        dicts ordered by relevance (best first). Returns ``[]`` for a blank
        query so callers never need to guard separately.
        """
        if not q.strip():
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT s.id, s.title,
                       snippet(messages_fts, 0, '**', '**', '…', 20) AS snippet,
                       bm25(messages_fts) AS score
                FROM messages_fts
                JOIN messages m ON m.rowid = messages_fts.rowid
                JOIN sessions s ON s.id = m.session_id
                WHERE messages_fts MATCH ?
                ORDER BY score
                LIMIT ?
                """,
                (q, limit),
            ).fetchall()
        return [
            {"session_id": r["id"], "title": r["title"], "snippet": r["snippet"]}
            for r in rows
        ]

    # ── pub/sub for SSE ──────────────────────────────────────────────

    def publish(self, session_id: str, event: SessionEvent) -> None:
        """Fan an event out to every live SSE subscriber on this
        session. Silently drops when there are no subscribers — early
        traces routinely fire before the UI's EventSource attaches,
        and a reply after the user has navigated away is harmless."""
        with self._lock:
            subscribers = list(self._subscribers.get(session_id, ()))
        for q in subscribers:
            # Queues are unbounded by default; put_nowait is effectively
            # non-blocking. If a subscriber is stuck, events accumulate
            # rather than blocking the publisher. OK for MVP.
            q.put_nowait(event)

    async def subscribe(self, session_id: str) -> AsyncIterator[SessionEvent]:
        """Async iterator of events for one SSE subscriber.

        Yields until either the client disconnects (the generator is
        closed) or a ``None`` sentinel is pushed (session reset /
        delete wake the queue so the caller can unwind).
        """
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

    # ── HITL pending futures ─────────────────────────────────────────

    def register_pending(
        self, session_id: str, request_id: str
    ) -> asyncio.Future[str]:
        """Create + register a Future for a HITL request. ``ask_user``
        awaits it; ``resolve_pending`` / ``cancel_pending`` complete it."""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        with self._lock:
            session_pending = self._pending.setdefault(session_id, {})
            if request_id in session_pending:
                raise ValueError(f"request_id already pending: {request_id!r}")
            session_pending[request_id] = fut
        return fut

    def resolve_pending(
        self, session_id: str, request_id: str, answer: str
    ) -> bool:
        """Resolve a waiting ``ask_user``. Returns True iff the request
        was pending and still live; False otherwise (stale / never
        existed / already resolved). The ``/respond`` endpoint uses
        this to 404 stale clicks."""
        with self._lock:
            session_pending = self._pending.get(session_id)
            if session_pending is None:
                return False
            fut = session_pending.pop(request_id, None)
            if not session_pending:
                self._pending.pop(session_id, None)
        if fut is None or fut.done():
            return False
        fut.set_result(answer)
        return True

    def cancel_pending(self, session_id: str, request_id: str) -> bool:
        """Cancel a pending Future (e.g. on timeout). Returns True iff
        it was present and still live."""
        with self._lock:
            session_pending = self._pending.get(session_id)
            if session_pending is None:
                return False
            fut = session_pending.pop(request_id, None)
            if not session_pending:
                self._pending.pop(session_id, None)
        if fut is None or fut.done():
            return False
        fut.cancel()
        return True

    def _cancel_all_pending(self, session_id: str) -> None:
        """Called on reset/delete to drop every in-flight ask_user for
        this session. Best-effort: already-done futures are skipped."""
        with self._lock:
            session_pending = self._pending.pop(session_id, None)
        if not session_pending:
            return
        for fut in session_pending.values():
            if not fut.done():
                fut.cancel()
