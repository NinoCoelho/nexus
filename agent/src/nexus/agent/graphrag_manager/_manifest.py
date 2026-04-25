"""Content-hash manifest helpers for GraphRAG incremental indexing."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

_manifest_db: sqlite3.Connection | None = None


def get_manifest_db() -> sqlite3.Connection | None:
    return _manifest_db


def open_manifest(db_dir: Path) -> sqlite3.Connection:
    global _manifest_db
    if _manifest_db is not None:
        return _manifest_db
    _manifest_db = sqlite3.connect(
        str(db_dir / "graphrag_manifest.sqlite"), check_same_thread=False,
    )
    _manifest_db.execute("PRAGMA journal_mode=WAL")
    _manifest_db.execute("""
        CREATE TABLE IF NOT EXISTS content_hashes (
            source_path TEXT PRIMARY KEY,
            content_hash TEXT NOT NULL,
            indexed_at REAL NOT NULL
        )
    """)
    _manifest_db.commit()
    return _manifest_db


def close_manifest() -> None:
    global _manifest_db
    if _manifest_db is not None:
        _manifest_db.close()
        _manifest_db = None


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def is_indexed(path: str, content: str) -> bool:
    if _manifest_db is None:
        return False
    row = _manifest_db.execute(
        "SELECT content_hash FROM content_hashes WHERE source_path = ?", (path,),
    ).fetchone()
    return row is not None and row[0] == _content_hash(content)


def mark_indexed(path: str, content: str) -> None:
    if _manifest_db is None:
        return
    _manifest_db.execute(
        "INSERT OR REPLACE INTO content_hashes (source_path, content_hash, indexed_at) "
        "VALUES (?, ?, ?)",
        (path, _content_hash(content), time.time()),
    )
    _manifest_db.commit()


def clear_manifest() -> None:
    if _manifest_db is None:
        return
    _manifest_db.execute("DELETE FROM content_hashes")
    _manifest_db.commit()
