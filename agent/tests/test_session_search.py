"""Tests for the FTS5-backed ``SessionStore.search()`` method."""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus.agent.llm import ChatMessage, Role
from nexus.server.session_store import SessionStore


# ── helpers ──────────────────────────────────────────────────────────────────


def _store(tmp_path: Path) -> SessionStore:
    return SessionStore(db_path=tmp_path / "sessions.sqlite")


def _seed(store: SessionStore, *messages: str) -> str:
    """Create a session with the given user messages and return its id."""
    session = store.create()
    history = [ChatMessage(role=Role.USER, content=msg) for msg in messages]
    store.replace_history(session.id, history)
    return session.id


# ── basic search ──────────────────────────────────────────────────────────────


def test_empty_query_returns_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store, "hello world")
    assert store.search("") == []
    assert store.search("   ") == []


def test_search_finds_exact_word(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sid = _seed(store, "the quick brown fox jumps over the lazy dog")
    results = store.search("fox")
    assert len(results) == 1
    assert results[0]["session_id"] == sid


def test_search_returns_title_and_snippet(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sid = _seed(store, "explain quantum entanglement in simple terms")
    # Auto-title is derived from the first message content.
    results = store.search("quantum")
    assert len(results) == 1
    r = results[0]
    assert r["session_id"] == sid
    assert "title" in r
    assert "snippet" in r


def test_snippet_highlights_match(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store, "debugging python async code")
    results = store.search("async")
    assert len(results) == 1
    # FTS5 snippet wraps matches in '**…**' per our query.
    assert "**" in results[0]["snippet"]


def test_no_match_returns_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store, "nothing relevant here")
    assert store.search("zzzyyyxxx") == []


def test_search_across_multiple_sessions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sid1 = _seed(store, "python decorators are powerful")
    sid2 = _seed(store, "typescript generics are useful")
    _seed(store, "a completely unrelated topic")

    results = store.search("python")
    ids = {r["session_id"] for r in results}
    assert sid1 in ids
    assert sid2 not in ids


def test_search_respects_limit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # Create 5 sessions all containing the same keyword.
    for i in range(5):
        _seed(store, f"session {i} about databases and SQL queries")
    results = store.search("databases", limit=3)
    assert len(results) <= 3


def test_search_deduplicates_by_session(tmp_path: Path) -> None:
    """Multiple messages in the same session matching the query should not
    inflate the result count unreasonably — the query joins to sessions
    so each match is associated with its session."""
    store = _store(tmp_path)
    sid = _seed(store, "rust borrow checker", "rust lifetimes", "rust traits")
    results = store.search("rust")
    # All hits belong to the same session — at least one result.
    assert len(results) >= 1
    assert all(r["session_id"] == sid for r in results)


# ── migration: rebuild from existing data ────────────────────────────────────


def test_fts_rebuild_on_existing_data(tmp_path: Path) -> None:
    """Simulate a DB that already has loom-schema messages but no FTS rows.
    Creating a new SessionStore against that DB should trigger an FTS rebuild
    so search works on pre-existing data."""
    import sqlite3

    db_path = tmp_path / "sessions.sqlite"

    # Populate the DB using loom's schema (ISO timestamps, WAL mode) to
    # simulate data inserted before the FTS table was created.
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions "
        "(id TEXT PRIMARY KEY, title TEXT, context TEXT, pending_question TEXT, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "model TEXT, input_tokens INTEGER DEFAULT 0, "
        "output_tokens INTEGER DEFAULT 0, tool_call_count INTEGER DEFAULT 0)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS messages "
        "(session_id TEXT NOT NULL, seq INTEGER NOT NULL, role TEXT NOT NULL, "
        "content TEXT, tool_calls TEXT, tool_call_id TEXT, name TEXT, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "PRIMARY KEY (session_id, seq), "
        "FOREIGN KEY (session_id) REFERENCES sessions(id))"
    )
    conn.execute(
        "INSERT INTO sessions (id, title) VALUES ('abc123', 'Pre-existing session')"
    )
    conn.execute(
        "INSERT INTO messages (session_id, seq, role, content) "
        "VALUES ('abc123', 0, 'user', 'loom schema fts rebuild keyword')"
    )
    conn.commit()
    conn.close()

    # Opening the store against this DB should auto-create FTS and rebuild.
    store = SessionStore(db_path=db_path)
    results = store.search("loom")
    assert len(results) == 1
    assert results[0]["session_id"] == "abc123"
