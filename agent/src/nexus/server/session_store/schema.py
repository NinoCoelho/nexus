"""SQLite schema helpers — FTS5 DDL and one-shot pre-loom migration.

Exports:
- ``_FTS_SCHEMA`` — DDL string for FTS5 virtual table + sync triggers.
- ``init_fts(loom_store)`` — creates the FTS5 table on an existing loom store.
- ``migrate_legacy_schema(db_path)`` — migrates the old integer-timestamp schema
  to loom's ISO-timestamp schema (runs once at startup if needed).
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loom.store.session import SessionStore as LoomSessionStore

log = logging.getLogger(__name__)

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


def init_fts(loom_store: LoomSessionStore) -> None:
    """Create the FTS5 virtual table + sync triggers if missing."""
    db = loom_store._db
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


# ── Migration from pre-loom schema ───────────────────────────────────────────


def migrate_legacy_schema(db_path: Path) -> None:
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
