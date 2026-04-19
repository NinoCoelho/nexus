"""SQLite-backed session store for Nexus."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

from ..agent.llm import ChatMessage, Role, ToolCall


_DB_PATH = Path("~/.nexus/sessions.sqlite").expanduser()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    context TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
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
"""


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
        self._init_db()

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

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

    def delete(self, session_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def rename(self, session_id: str, title: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?", (title, int(time.time()), session_id))
