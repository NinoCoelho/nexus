"""FTS5-backed full-text search index for the vault.

Index lives at ~/.nexus/vault_index.sqlite.
Thread-safe via a module-level Lock.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_INDEX_PATH = Path("~/.nexus/vault_index.sqlite").expanduser()
_VAULT_ROOT = Path("~/.nexus/vault").expanduser()
_lock = threading.Lock()

_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS vault_fts USING fts5(
    path UNINDEXED,
    body,
    tokenize='porter unicode61'
);
"""


def _connect() -> sqlite3.Connection:
    _INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(_INDEX_PATH), check_same_thread=False)
    con.execute(_DDL)
    con.commit()
    return con


def _norm_path(path: str) -> str:
    """Normalise path relative to vault root (strip leading slash/dots)."""
    p = Path(path)
    if p.is_absolute():
        try:
            p = p.relative_to(_VAULT_ROOT.expanduser())
        except ValueError:
            pass
    return str(p)


def _escape_query(query: str) -> str:
    """Wrap every token in double-quotes so raw FTS5 operators are rejected."""
    tokens = re.findall(r'\S+', query)
    if not tokens:
        return '""'
    return " ".join(f'"{t}"' for t in tokens)


# ── Public API ────────────────────────────────────────────────────────────────

def index_path(path: str, body: str) -> None:
    """Upsert a document (delete + insert, FTS5 has no UPDATE)."""
    norm = _norm_path(path)
    with _lock:
        con = _connect()
        try:
            con.execute("DELETE FROM vault_fts WHERE path = ?", (norm,))
            con.execute("INSERT INTO vault_fts(path, body) VALUES (?, ?)", (norm, body))
            con.commit()
        finally:
            con.close()


def remove_path(path: str) -> None:
    """Remove a document from the index."""
    norm = _norm_path(path)
    with _lock:
        con = _connect()
        try:
            con.execute("DELETE FROM vault_fts WHERE path = ?", (norm,))
            con.commit()
        finally:
            con.close()


def rename_path(from_path: str, to_path: str) -> None:
    """Rename a document in the index (fetch body, re-insert under new path)."""
    from_norm = _norm_path(from_path)
    to_norm = _norm_path(to_path)
    with _lock:
        con = _connect()
        try:
            row = con.execute(
                "SELECT body FROM vault_fts WHERE path = ?", (from_norm,)
            ).fetchone()
            con.execute("DELETE FROM vault_fts WHERE path = ?", (from_norm,))
            if row:
                con.execute(
                    "INSERT INTO vault_fts(path, body) VALUES (?, ?)", (to_norm, row[0])
                )
            con.commit()
        finally:
            con.close()


def search(query: str, limit: int = 50) -> list[dict]:
    """Search the index; returns list of {path, snippet, score}."""
    query = query.strip()
    if not query:
        return []
    escaped = _escape_query(query)
    with _lock:
        con = _connect()
        try:
            rows = con.execute(
                """
                SELECT
                    path,
                    snippet(vault_fts, 1, '<mark>', '</mark>', '…', 20),
                    rank
                FROM vault_fts
                WHERE vault_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (escaped, limit),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            log.warning("vault_search: query error: %s", exc)
            return []
        finally:
            con.close()
    return [{"path": r[0], "snippet": r[1], "score": r[2]} for r in rows]


def rebuild_from_disk() -> int:
    """Re-index all .md files under the vault root. Returns file count."""
    vault_root = _VAULT_ROOT
    vault_root.mkdir(parents=True, exist_ok=True)

    files = list(vault_root.rglob("*.md"))
    with _lock:
        con = _connect()
        try:
            con.execute("DELETE FROM vault_fts")
            count = 0
            for fp in files:
                try:
                    body = fp.read_text(encoding="utf-8", errors="replace")
                    rel = str(fp.relative_to(vault_root))
                    con.execute(
                        "INSERT INTO vault_fts(path, body) VALUES (?, ?)", (rel, body)
                    )
                    count += 1
                except OSError as exc:
                    log.warning("vault_search: skipping %s: %s", fp, exc)
            con.commit()
        finally:
            con.close()
    return count


def is_empty() -> bool:
    """Return True if the index has no documents."""
    with _lock:
        con = _connect()
        try:
            row = con.execute("SELECT COUNT(*) FROM vault_fts").fetchone()
            return (row[0] if row else 0) == 0
        finally:
            con.close()
