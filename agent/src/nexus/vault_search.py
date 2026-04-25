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

_META_DDL = """
CREATE TABLE IF NOT EXISTS file_meta (
    path TEXT PRIMARY KEY,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    _INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(_INDEX_PATH), check_same_thread=False)
    con.execute(_DDL)
    con.execute(_META_DDL)
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
    size = len(body.encode("utf-8", errors="replace"))
    fp = _VAULT_ROOT / norm
    try:
        mtime = fp.stat().st_mtime
    except OSError:
        mtime = 0.0
    with _lock:
        con = _connect()
        try:
            con.execute("DELETE FROM vault_fts WHERE path = ?", (norm,))
            con.execute("INSERT INTO vault_fts(path, body) VALUES (?, ?)", (norm, body))
            con.execute(
                "INSERT OR REPLACE INTO file_meta(path, mtime, size) VALUES (?, ?, ?)",
                (norm, mtime, size),
            )
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
            con.execute("DELETE FROM file_meta WHERE path = ?", (norm,))
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
            con.execute("DELETE FROM file_meta WHERE path = ?", (from_norm,))
            if row:
                con.execute(
                    "INSERT INTO vault_fts(path, body) VALUES (?, ?)", (to_norm, row[0])
                )
                fp = _VAULT_ROOT / to_norm
                try:
                    mtime = fp.stat().st_mtime
                    size = fp.stat().st_size
                except OSError:
                    mtime, size = 0.0, len(row[0])
                con.execute(
                    "INSERT OR REPLACE INTO file_meta(path, mtime, size) VALUES (?, ?, ?)",
                    (to_norm, mtime, size),
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


def rebuild_from_disk(full: bool = False) -> int:
    """Re-index .md files under the vault root.

    With ``full=True`` (escape hatch), drop everything and rebuild.
    With ``full=False`` (default), use ``file_meta(path, mtime, size)`` to
    skip files whose on-disk mtime+size match the index, re-index changed
    or new files, and prune rows for files that no longer exist on disk.

    Returns the number of files now indexed (total, not delta).
    """
    vault_root = _VAULT_ROOT
    vault_root.mkdir(parents=True, exist_ok=True)

    files = list(vault_root.rglob("*.md"))
    with _lock:
        con = _connect()
        try:
            if full:
                con.execute("DELETE FROM vault_fts")
                con.execute("DELETE FROM file_meta")

            existing: dict[str, tuple[float, int]] = {}
            if not full:
                for row in con.execute("SELECT path, mtime, size FROM file_meta"):
                    existing[row[0]] = (row[1], row[2])

            disk_paths: set[str] = set()
            indexed_total = 0
            for fp in files:
                try:
                    rel = str(fp.relative_to(vault_root))
                    disk_paths.add(rel)
                    st = fp.stat()
                    if not full:
                        prev = existing.get(rel)
                        if prev is not None and prev[0] == st.st_mtime and prev[1] == st.st_size:
                            indexed_total += 1
                            continue
                    body = fp.read_text(encoding="utf-8", errors="replace")
                    con.execute("DELETE FROM vault_fts WHERE path = ?", (rel,))
                    con.execute(
                        "INSERT INTO vault_fts(path, body) VALUES (?, ?)", (rel, body)
                    )
                    con.execute(
                        "INSERT OR REPLACE INTO file_meta(path, mtime, size) VALUES (?, ?, ?)",
                        (rel, st.st_mtime, st.st_size),
                    )
                    indexed_total += 1
                except OSError as exc:
                    log.warning("vault_search: skipping %s: %s", fp, exc)

            if not full:
                stale = set(existing.keys()) - disk_paths
                for rel in stale:
                    con.execute("DELETE FROM vault_fts WHERE path = ?", (rel,))
                    con.execute("DELETE FROM file_meta WHERE path = ?", (rel,))

            con.commit()
        finally:
            con.close()
    return indexed_total


def is_empty() -> bool:
    """Return True if the index has no documents."""
    with _lock:
        con = _connect()
        try:
            row = con.execute("SELECT COUNT(*) FROM vault_fts").fetchone()
            return (row[0] if row else 0) == 0
        finally:
            con.close()
