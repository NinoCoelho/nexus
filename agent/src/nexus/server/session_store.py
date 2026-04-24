"""Session store — Nexus-side composition layer over ``loom.store.session.SessionStore``.

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

Type boundary — Nexus uses ``nexus.agent.llm.ChatMessage`` / ``ToolCall``
which have ``arguments: dict``.  Loom uses ``loom.types.ChatMessage`` /
``ToolCall`` which have ``arguments: str`` (JSON).  The ``_to_loom_msg``
and ``_from_loom_msg`` helpers convert at the boundary so the rest of the
code stays unaware.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sqlite3
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from loom.hitl import HitlBroker
from loom.store.session import SessionStore as LoomSessionStore

from ..agent.llm import ChatMessage, Role, ToolCall
from .events import SessionEvent

log = logging.getLogger(__name__)

_DB_PATH = Path("~/.nexus/sessions.sqlite").expanduser()

# ── Nexus FTS5 schema (appended to loom's tables) ────────────────────────────

_FTS_SCHEMA = """
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


# ── Dataclasses consumed by server handlers ───────────────────────────────────


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


# ── Type conversion helpers ───────────────────────────────────────────────────


def _to_loom_msg(msg: ChatMessage) -> "loom.types.ChatMessage":  # type: ignore[name-defined]
    """Convert a Nexus ChatMessage to loom's format.

    Nexus ``ToolCall.arguments`` is a ``dict``; loom expects a JSON string.
    """
    import loom.types as lt

    loom_tcs: list[lt.ToolCall] | None = None
    if msg.tool_calls:
        loom_tcs = [
            lt.ToolCall(
                id=tc.id,
                name=tc.name,
                arguments=json.dumps(tc.arguments) if isinstance(tc.arguments, dict) else tc.arguments,
            )
            for tc in msg.tool_calls
        ]
    return lt.ChatMessage(
        role=lt.Role(msg.role.value),
        content=msg.content,
        tool_calls=loom_tcs,
        tool_call_id=msg.tool_call_id,
        name=msg.name,
    )


def _from_loom_msg(msg: "loom.types.ChatMessage") -> ChatMessage:  # type: ignore[name-defined]
    """Convert loom's ChatMessage to Nexus's format.

    Loom ``ToolCall.arguments`` is a JSON string; Nexus expects a ``dict``.
    """
    nexus_tcs: list[ToolCall] = []
    if msg.tool_calls:
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments
            except (TypeError, json.JSONDecodeError):
                args = {}
            nexus_tcs.append(ToolCall(id=tc.id, name=tc.name, arguments=args))
    return ChatMessage(
        role=Role(msg.role.value),
        content=msg.content,
        tool_calls=nexus_tcs,
        tool_call_id=msg.tool_call_id,
        name=msg.name,
    )


def _ts_to_int(ts: Any) -> int:
    """Convert a timestamp value (ISO string or integer) to a Unix epoch int."""
    if ts is None:
        return 0
    if isinstance(ts, (int, float)):
        return int(ts)
    # ISO string from SQLite CURRENT_TIMESTAMP: "2024-01-15 10:30:00"
    try:
        dt = datetime.fromisoformat(str(ts).replace(" ", "T"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, AttributeError):
        return 0


# ── Migration from pre-loom schema ───────────────────────────────────────────


def _migrate_legacy_schema(db_path: Path) -> None:
    """One-shot migration from Nexus's pre-loom schema to loom's schema.

    Detects the old schema by the presence of an integer ``created_at``
    on the ``sessions`` table (the legacy schema stored epoch seconds as
    NOT NULL INTEGER, while loom stores ISO timestamps via CURRENT_TIMESTAMP).

    Strategy:
    1. Backup the file (idempotent — won't overwrite an existing backup).
    2. Read all session and message rows from the old tables.
    3. DROP the old tables (so loom can re-create them with the correct schema).
    4. Let the caller's ``LoomSessionStore(db_path)`` create fresh tables.
    5. Insert migrated rows into the fresh tables (done in ``__init__`` after
       loom is initialised).

    To avoid a two-step init, the migrated data is written to a temporary
    pickle-style in-memory structure and returned; the caller inserts it.
    Actually — simpler: we do the full migration here by opening loom ourselves
    after dropping the old tables.
    """
    bak = db_path.with_suffix(".sqlite.pre-loom-migration.bak")
    try:
        # --- detect legacy format ---
        conn = sqlite3.connect(str(db_path))
        try:
            cols = {
                row[1]: row[2]  # name -> type
                for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
            }
        finally:
            conn.close()

        if "created_at" not in cols:
            return  # no sessions table — fresh DB

        # Legacy schema used "INTEGER NOT NULL" for created_at.
        # Loom uses "TIMESTAMP" (DEFAULT CURRENT_TIMESTAMP, i.e. an ISO string).
        if "INTEGER" not in cols["created_at"].upper():
            return  # already loom format

        # --- backup (idempotent) ---
        if not bak.exists():
            shutil.copy2(str(db_path), str(bak))
            log.info("session migration: backed up %s → %s", db_path, bak)

        # --- read legacy data ---
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            # Read sessions — handle old DBs that may lack some columns.
            try:
                sess_rows = conn.execute(
                    "SELECT id, title, context, created_at, updated_at, model, "
                    "input_tokens, output_tokens, tool_call_count FROM sessions"
                ).fetchall()
            except sqlite3.OperationalError:
                sess_rows = conn.execute(
                    "SELECT id, title, context, created_at, updated_at FROM sessions"
                ).fetchall()
            msg_rows = conn.execute(
                "SELECT session_id, seq, role, content, tool_calls, tool_call_id, created_at "
                "FROM messages ORDER BY session_id, seq"
            ).fetchall()
        finally:
            conn.close()

        def _int_to_iso(ts: Any) -> str:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        def _col(row: sqlite3.Row, name: str, default: Any = None) -> Any:
            try:
                return row[name]
            except (IndexError, KeyError):
                return default

        # --- drop old tables so loom can re-create them with correct schema ---
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("DROP TABLE IF EXISTS messages")
            conn.execute("DROP TABLE IF EXISTS sessions")
            conn.commit()
        finally:
            conn.close()

        # --- let loom create fresh tables ---
        loom = LoomSessionStore(db_path)

        # --- insert migrated data ---
        for s in sess_rows:
            created_iso = _int_to_iso(s["created_at"])
            updated_iso = _int_to_iso(s["updated_at"])
            loom._db.execute(
                "INSERT OR IGNORE INTO sessions "
                "(id, title, context, created_at, updated_at, model, "
                " input_tokens, output_tokens, tool_call_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    s["id"],
                    s["title"] or "New session",
                    _col(s, "context"),
                    created_iso,
                    updated_iso,
                    _col(s, "model"),
                    _col(s, "input_tokens", 0),
                    _col(s, "output_tokens", 0),
                    _col(s, "tool_call_count", 0),
                ),
            )
        loom._db.commit()

        for m in msg_rows:
            created_iso = _int_to_iso(m["created_at"])
            loom._db.execute(
                "INSERT OR IGNORE INTO messages "
                "(session_id, seq, role, content, tool_calls, tool_call_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    m["session_id"],
                    m["seq"],
                    m["role"],
                    m["content"],
                    m["tool_calls"],
                    m["tool_call_id"],
                    created_iso,
                ),
            )
        loom._db.commit()
        loom._db.close()

        log.info(
            "session migration: migrated %d sessions, %d messages from legacy schema",
            len(sess_rows),
            len(msg_rows),
        )

    except Exception:
        log.exception(
            "session migration FAILED — legacy data preserved at %s; "
            "new data will go into loom's schema going forward",
            bak,
        )


# ── Nexus SessionStore ────────────────────────────────────────────────────────


class SessionStore:
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
            _migrate_legacy_schema(self._db_path)

        # Loom handles all schema creation and persistence.
        self._loom = LoomSessionStore(self._db_path)

        # Add FTS5 on top of loom's messages table so search has snippets.
        self._init_fts()

        self._lock = Lock()
        self._subscribers: dict[str, list[asyncio.Queue[SessionEvent | None]]] = {}
        self._broker = HitlBroker(
            publish_hook=lambda sid, ev: self.publish(
                sid, SessionEvent(kind=ev.kind, data=dict(ev.data))
            )
        )

    @property
    def broker(self) -> HitlBroker:
        return self._broker

    def _connect(self) -> sqlite3.Connection:
        """Open a read-only-ish connection for ad-hoc queries (e.g. InsightsEngine)."""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_fts(self) -> None:
        """Create the FTS5 virtual table + sync triggers if missing."""
        db = self._loom._db
        fts_existed = bool(
            db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages_fts'"
            ).fetchone()
        )
        db.executescript(_FTS_SCHEMA)
        # Backfill FTS for existing messages when the table was just created.
        if not fts_existed:
            msg_count = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            if msg_count > 0:
                db.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")
                db.commit()

    # ── persistence — delegate to loom ───────────────────────────────────────

    def create(self, context: str | None = None) -> Session:
        sid = uuid.uuid4().hex
        d = self._loom.get_or_create(sid, title="New session", context=context)
        return Session(id=d["id"], title=d["title"] or "New session", context=context)

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

    def list(self, limit: int = 50) -> list[SessionSummary]:
        rows = self._loom._db.execute(
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
            tool_calls=tcs or None,
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
        self._cancel_all_pending(session_id)

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

    def search(self, q: str, *, limit: int = 20) -> list[dict]:
        """Full-text search with BM25 ranking and snippet highlighting.

        Returns ``[{"session_id": str, "title": str, "snippet": str}]``.
        Falls back to an empty list on blank queries.
        """
        if not q.strip():
            return []
        try:
            rows = self._loom._db.execute(
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
        row = self._loom._db.execute(
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
        with self._lock:
            self._loom._db.execute(
                "INSERT INTO sessions (id, title, context, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, title, context, created_iso, created_iso),
            )
            for seq, msg in enumerate(messages):
                tc_json: str | None = None
                if msg.tool_calls:
                    tc_json = json.dumps([
                        {"id": tc.id, "name": tc.name, "arguments": json.dumps(tc.arguments) if isinstance(tc.arguments, dict) else tc.arguments}
                        for tc in msg.tool_calls
                    ])
                self._loom._db.execute(
                    "INSERT INTO messages (session_id, seq, role, content, tool_calls, tool_call_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (session_id, seq, msg.role.value, msg.content or "", tc_json, msg.tool_call_id, created_iso),
                )
            self._loom._db.commit()

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
