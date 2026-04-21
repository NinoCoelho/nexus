"""Test one-shot migration from the pre-loom Nexus schema to loom's schema.

The legacy Nexus schema used:
- INTEGER NOT NULL for created_at / updated_at (Unix epoch seconds)
- No WAL mode
- No pending_question column

The migration should:
1. Detect the old schema (INTEGER created_at)
2. Copy sessions.sqlite → sessions.sqlite.pre-loom-migration.bak
3. Migrate all session rows with ISO-format timestamps
4. Migrate all message rows
5. Leave data readable via the new SessionStore API
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from nexus.agent.llm import ChatMessage, Role
from nexus.server.session_store import SessionStore


def _build_legacy_db(db_path: Path, now: int) -> None:
    """Populate a SQLite file in the legacy (pre-loom) Nexus schema."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "CREATE TABLE sessions ("
        "  id TEXT PRIMARY KEY, "
        "  title TEXT NOT NULL, "
        "  context TEXT, "
        "  created_at INTEGER NOT NULL, "
        "  updated_at INTEGER NOT NULL, "
        "  model TEXT, "
        "  input_tokens INTEGER NOT NULL DEFAULT 0, "
        "  output_tokens INTEGER NOT NULL DEFAULT 0, "
        "  tool_call_count INTEGER NOT NULL DEFAULT 0"
        ")"
    )
    conn.execute(
        "CREATE TABLE messages ("
        "  session_id TEXT NOT NULL, "
        "  seq INTEGER NOT NULL, "
        "  role TEXT NOT NULL, "
        "  content TEXT NOT NULL, "
        "  tool_calls TEXT, "
        "  tool_call_id TEXT, "
        "  created_at INTEGER NOT NULL, "
        "  PRIMARY KEY (session_id, seq), "
        "  FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE"
        ")"
    )
    conn.execute(
        "INSERT INTO sessions (id, title, context, created_at, updated_at, model, "
        "  input_tokens, output_tokens, tool_call_count) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("sess_alpha", "Alpha session", "some context", now - 3600, now, "gpt-4", 100, 200, 3),
    )
    conn.execute(
        "INSERT INTO sessions (id, title, context, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("sess_beta", "Beta session", None, now - 7200, now - 1800),
    )
    conn.execute(
        "INSERT INTO messages (session_id, seq, role, content, tool_calls, tool_call_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("sess_alpha", 0, "user", "hello from legacy schema", None, None, now - 3600),
    )
    conn.execute(
        "INSERT INTO messages (session_id, seq, role, content, tool_calls, tool_call_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("sess_alpha", 1, "assistant", "reply from legacy schema", None, None, now - 3590),
    )
    conn.execute(
        "INSERT INTO messages (session_id, seq, role, content, tool_calls, tool_call_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("sess_beta", 0, "user", "beta session message", None, None, now - 7200),
    )
    conn.commit()
    conn.close()


def test_migration_creates_backup(tmp_path: Path) -> None:
    """The backup file is created before any data is touched."""
    db_path = tmp_path / "sessions.sqlite"
    bak_path = db_path.with_suffix(".sqlite.pre-loom-migration.bak")

    _build_legacy_db(db_path, int(time.time()))

    assert not bak_path.exists()
    SessionStore(db_path=db_path)  # triggers migration
    assert bak_path.exists(), "backup file must be created during migration"


def test_migration_backup_is_idempotent(tmp_path: Path) -> None:
    """Running migration twice doesn't overwrite the original backup."""
    db_path = tmp_path / "sessions.sqlite"
    bak_path = db_path.with_suffix(".sqlite.pre-loom-migration.bak")
    now = int(time.time())

    _build_legacy_db(db_path, now)
    SessionStore(db_path=db_path)  # first migration

    bak_mtime = bak_path.stat().st_mtime

    # Re-run (store already migrated — no-op).
    SessionStore(db_path=db_path)

    assert bak_path.stat().st_mtime == bak_mtime, "backup must not be overwritten on second init"


def test_migration_sessions_readable(tmp_path: Path) -> None:
    """Migrated sessions appear in list() and get() after migration."""
    db_path = tmp_path / "sessions.sqlite"
    now = int(time.time())
    _build_legacy_db(db_path, now)

    store = SessionStore(db_path=db_path)

    summaries = store.list(limit=50)
    ids = {s.id for s in summaries}
    assert "sess_alpha" in ids
    assert "sess_beta" in ids


def test_migration_messages_readable(tmp_path: Path) -> None:
    """Messages from the legacy schema are accessible via get() after migration."""
    db_path = tmp_path / "sessions.sqlite"
    now = int(time.time())
    _build_legacy_db(db_path, now)

    store = SessionStore(db_path=db_path)

    sess = store.get("sess_alpha")
    assert sess is not None
    assert len(sess.history) == 2
    assert sess.history[0].role == Role.USER
    assert sess.history[0].content == "hello from legacy schema"
    assert sess.history[1].role == Role.ASSISTANT
    assert sess.history[1].content == "reply from legacy schema"


def test_migration_context_preserved(tmp_path: Path) -> None:
    """Session context is preserved through the migration."""
    db_path = tmp_path / "sessions.sqlite"
    now = int(time.time())
    _build_legacy_db(db_path, now)

    store = SessionStore(db_path=db_path)
    sess = store.get("sess_alpha")
    assert sess is not None
    assert sess.context == "some context"


def test_migration_timestamps_preserved(tmp_path: Path) -> None:
    """Timestamps (created_at / updated_at) survive migration as Unix ints."""
    db_path = tmp_path / "sessions.sqlite"
    now = int(time.time())
    _build_legacy_db(db_path, now)

    store = SessionStore(db_path=db_path)
    summaries = {s.id: s for s in store.list(limit=50)}

    alpha = summaries["sess_alpha"]
    # Allow ±2 seconds of rounding from ISO string round-trip.
    assert abs(alpha.created_at - (now - 3600)) <= 2
    assert abs(alpha.updated_at - now) <= 2


def test_migration_fresh_db_no_bak(tmp_path: Path) -> None:
    """A fresh (non-existent) DB path doesn't create a backup."""
    db_path = tmp_path / "brand_new.sqlite"
    bak_path = db_path.with_suffix(".sqlite.pre-loom-migration.bak")

    SessionStore(db_path=db_path)

    assert not bak_path.exists(), "no backup should be created for a fresh DB"


def test_migration_post_create_works(tmp_path: Path) -> None:
    """After migration, new sessions can be created and queried normally."""
    db_path = tmp_path / "sessions.sqlite"
    now = int(time.time())
    _build_legacy_db(db_path, now)

    store = SessionStore(db_path=db_path)

    new = store.create(context="post-migration")
    assert new.id
    assert new.context == "post-migration"

    retrieved = store.get(new.id)
    assert retrieved is not None
    assert retrieved.context == "post-migration"


def test_migration_search_works_after_migration(tmp_path: Path) -> None:
    """FTS5 search works on migrated message content."""
    db_path = tmp_path / "sessions.sqlite"
    now = int(time.time())
    _build_legacy_db(db_path, now)

    store = SessionStore(db_path=db_path)

    results = store.search("legacy")
    session_ids = {r["session_id"] for r in results}
    assert "sess_alpha" in session_ids
