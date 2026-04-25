"""FTS5 search and import helpers for SessionStore.

Extracted as a mixin to keep ``store.py`` under 300 LOC.
Concrete class must have ``self._loom`` and ``self._lock`` already set up.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from ...agent.llm import ChatMessage
from .models import _ts_to_int


class QueryMixin:
    """Mixin providing FTS5 full-text search, timestamp lookup, and session import."""

    def search(self, q: str, *, limit: int = 20) -> list[dict]:
        """Full-text search with BM25 ranking and snippet highlighting.

        Returns ``[{"session_id": str, "title": str, "snippet": str}]``.
        Falls back to an empty list on blank queries.
        """
        if not q.strip():
            return []
        try:
            rows = self._loom._db.execute(  # type: ignore[attr-defined]
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
        except sqlite3.OperationalError:
            # FTS5 table missing or query malformed — fall back to empty.
            return []
        seen: dict[str, dict] = {}
        for r in rows:
            sid = r[0]
            if sid not in seen:
                seen[sid] = {
                    "session_id": sid,
                    "title": r[1] or "New session",
                    "snippet": r[2] or "",
                }
        return list(seen.values())

    def get_session_timestamps(self, session_id: str) -> tuple[int, int]:
        """Return (created_at, updated_at) as Unix epoch integers for a session."""
        row = self._loom._db.execute(  # type: ignore[attr-defined]
            "SELECT created_at, updated_at FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return 0, 0
        return _ts_to_int(row[0]), _ts_to_int(row[1])

    def import_session(
        self,
        session_id: str,
        title: str,
        context: str | None,
        messages: list[ChatMessage],
        created_at: int,
    ) -> None:
        """Insert a new session with pre-built history (used by the import endpoint)."""
        created_iso = datetime.fromtimestamp(created_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:  # type: ignore[attr-defined]
            self._loom._db.execute(  # type: ignore[attr-defined]
                "INSERT INTO sessions (id, title, context, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, title, context, created_iso, created_iso),
            )
            for seq, msg in enumerate(messages):
                tc_json: str | None = None
                if msg.tool_calls:
                    tc_json = json.dumps([
                        {
                            "id": tc.id,
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments) if isinstance(tc.arguments, dict) else tc.arguments,
                        }
                        for tc in msg.tool_calls
                    ])
                self._loom._db.execute(  # type: ignore[attr-defined]
                    "INSERT INTO messages (session_id, seq, role, content, tool_calls, tool_call_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (session_id, seq, msg.role.value, msg.content or "", tc_json, msg.tool_call_id, created_iso),
                )
            self._loom._db.commit()  # type: ignore[attr-defined]
