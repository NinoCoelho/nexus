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

# Feedback table — Nexus-only, sits alongside loom's messages table.
# Keyed by (session_id, seq) so it survives history rewrites for a given
# turn. ``ON DELETE CASCADE`` would be ideal but loom doesn't enable
# foreign keys; we clean up explicitly when a session is deleted.
_FEEDBACK_SCHEMA = """
CREATE TABLE IF NOT EXISTS message_feedback (
    session_id TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    value      TEXT,
    pinned     INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (session_id, seq),
    CHECK(value IS NULL OR value IN ('up', 'down'))
);
"""

# Durable HITL event log — feeds the bell/notification center so the user can
# see prompts that fired (and possibly timed out) while no UI was attached.
# In-memory ``_pending`` futures live in pubsub.py and are lost on restart;
# this table is the user-visible audit trail.
_HITL_EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS hitl_events (
    request_id   TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    kind         TEXT NOT NULL,
    prompt       TEXT NOT NULL,
    payload_json TEXT,
    status       TEXT NOT NULL,
    answer       TEXT,
    reason       TEXT,
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at  TEXT,
    CHECK(status IN ('pending','answered','auto_answered','cancelled','timed_out'))
);
CREATE INDEX IF NOT EXISTS hitl_events_created_idx
    ON hitl_events(created_at DESC);
CREATE INDEX IF NOT EXISTS hitl_events_session_idx
    ON hitl_events(session_id);
"""

# Durable parked HITL request state — survives server restart so the agent
# can resume a turn after the user answers (potentially much) later.
# `parked_messages_json` is the snapshot of loom's all_messages up through the
# ASSISTANT message that contains the ask_user tool_call. On resume we append
# a TOOL message with the user's answer and run a fresh `run_turn_stream`
# from that history.
_HITL_PENDING_SCHEMA = """
CREATE TABLE IF NOT EXISTS hitl_pending (
    request_id           TEXT PRIMARY KEY,
    session_id           TEXT NOT NULL,
    tool_call_id         TEXT NOT NULL,
    kind                 TEXT NOT NULL,
    prompt               TEXT NOT NULL,
    choices_json         TEXT,
    fields_json          TEXT,
    form_title           TEXT,
    form_description     TEXT,
    "default"            TEXT,
    timeout_seconds      INTEGER,
    deadline_at          TEXT,
    created_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    parked_messages_json TEXT NOT NULL DEFAULT '[]',
    model_id             TEXT,
    status               TEXT NOT NULL DEFAULT 'parked',
    answered_at          TEXT,
    answer_json          TEXT,
    CHECK(status IN ('parked','answered','expired','cancelled'))
);
CREATE INDEX IF NOT EXISTS hitl_pending_session_idx
    ON hitl_pending(session_id, status);
CREATE INDEX IF NOT EXISTS hitl_pending_deadline_idx
    ON hitl_pending(deadline_at)
    WHERE deadline_at IS NOT NULL;
"""

# Browser Web Push subscriptions. Single-user app, so no user id —
# the endpoint is unique per browser/profile.
_PUSH_SUBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS push_subscriptions (
    endpoint     TEXT PRIMARY KEY,
    p256dh       TEXT NOT NULL,
    auth         TEXT NOT NULL,
    user_agent   TEXT,
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def _ensure_feedback_pinned_column(db) -> None:
    """Migrate older databases that pre-date the ``pinned`` column."""
    cols = {r[1] for r in db.execute("PRAGMA table_info(message_feedback)").fetchall()}
    if cols and "pinned" not in cols:
        db.execute(
            "ALTER TABLE message_feedback ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"
        )
        db.commit()


def _ensure_subagent_columns(db) -> None:
    """Migrate older databases that pre-date the spawn_subagents columns.

    Adds ``parent_session_id`` and ``hidden`` to the loom-owned ``sessions``
    table so child sessions spawned by the spawn_subagents tool can be linked
    back to the parent and hidden from the default sidebar listing.
    """
    cols = {r[1] for r in db.execute("PRAGMA table_info(sessions)").fetchall()}
    if not cols:
        return
    if "parent_session_id" not in cols:
        db.execute("ALTER TABLE sessions ADD COLUMN parent_session_id TEXT")
    if "hidden" not in cols:
        db.execute("ALTER TABLE sessions ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
    db.commit()


def init_fts(loom_store: LoomSessionStore) -> None:
    """Create the FTS5 virtual table + sync triggers if missing."""
    db = loom_store._db
    fts_existed = bool(
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages_fts'"
        ).fetchone()
    )
    db.executescript(_FTS_SCHEMA)
    db.executescript(_FEEDBACK_SCHEMA)
    db.executescript(_HITL_EVENTS_SCHEMA)
    db.executescript(_HITL_PENDING_SCHEMA)
    db.executescript(_PUSH_SUBS_SCHEMA)
    _ensure_feedback_pinned_column(db)
    _ensure_subagent_columns(db)
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
