"""Hidden per-folder index dir + manifest.

Layout per folder::

    <folder>/.nexus-graph/
        graphrag_chunks.sqlite      # owned by loom GraphRAGEngine
        graphrag_entities.sqlite    # owned by loom GraphRAGEngine
        graphrag_vectors.sqlite     # owned by loom GraphRAGEngine
        manifest.sqlite             # this module: per-file mtime/hash + meta kv

The manifest's ``meta`` table stores the ontology snapshot, schema version,
embedder identifier, and ontology hash so that ``initialize`` and
``stale_files`` can detect drift without rescanning every chunk.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
HIDDEN_DIR = ".nexus-graph"


def normalize_folder(folder: str | Path) -> Path:
    """Return an absolute, symlink-resolved Path for use as cache/tab key."""
    return Path(os.path.realpath(str(folder)))


def folder_dot_dir(folder: str | Path) -> Path:
    """Return ``<folder>/.nexus-graph`` (does not create it)."""
    return normalize_folder(folder) / HIDDEN_DIR


def is_initialized(folder: str | Path) -> bool:
    """True if the folder has a `.nexus-graph/manifest.sqlite` already."""
    return (folder_dot_dir(folder) / "manifest.sqlite").is_file()


def open_manifest(folder: str | Path) -> sqlite3.Connection:
    """Open (and create if needed) the per-folder manifest SQLite.

    The connection is owned by the engine pool entry — callers should not
    close it directly; let the pool's eviction handler do it.
    """
    dot = folder_dot_dir(folder)
    dot.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(dot / "manifest.sqlite"), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS files (
            rel_path TEXT PRIMARY KEY,
            mtime REAL NOT NULL,
            size INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            indexed_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    conn.commit()
    # Stamp schema version on first creation; subsequent opens are no-op.
    cur = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'")
    if cur.fetchone() is None:
        set_meta_kv(conn, "schema_version", str(SCHEMA_VERSION))
    return conn


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


def ontology_hash(ontology: dict[str, Any]) -> str:
    """Stable hash of the ontology — used to detect drift after edits."""
    canonical = json.dumps(
        {
            "entity_types": sorted(ontology.get("entity_types") or []),
            "relations": sorted(ontology.get("relations") or []),
            "allow_custom_relations": bool(ontology.get("allow_custom_relations", True)),
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_meta_kv(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta_kv(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value, updated_at) VALUES (?, ?, ?)",
        (key, value, time.time()),
    )
    conn.commit()


def load_meta(folder: str | Path) -> dict[str, Any]:
    """Read the saved ontology + meta values without keeping the conn open.

    Returns ``{}`` (i.e. ``exists=False``) when the folder has no index yet.
    """
    if not is_initialized(folder):
        return {}
    conn = sqlite3.connect(str(folder_dot_dir(folder) / "manifest.sqlite"))
    try:
        rows = conn.execute("SELECT key, value FROM meta").fetchall()
        kv = {k: v for k, v in rows}
        ontology_json = kv.get("ontology", "")
        ontology: dict[str, Any] = {}
        if ontology_json:
            try:
                ontology = json.loads(ontology_json)
            except json.JSONDecodeError:
                ontology = {}
        last_indexed_at: float | None = None
        # Most recently indexed file is a good "last indexed" proxy without
        # a separate field — ages with the data, not the schema.
        row = conn.execute("SELECT MAX(indexed_at) FROM files").fetchone()
        if row and row[0] is not None:
            last_indexed_at = float(row[0])
        file_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        return {
            "schema_version": int(kv.get("schema_version") or SCHEMA_VERSION),
            "ontology": ontology,
            "ontology_hash": kv.get("ontology_hash") or "",
            "embedder_id": kv.get("embedder_id") or "",
            "extractor_id": kv.get("extractor_id") or "",
            "last_indexed_at": last_indexed_at,
            "file_count": int(file_count or 0),
        }
    finally:
        conn.close()


def save_meta(folder: str | Path, *, ontology: dict[str, Any] | None = None,
              embedder_id: str | None = None, extractor_id: str | None = None) -> None:
    """Write subset of meta values. Pass only the fields you want to change."""
    conn = open_manifest(folder)
    try:
        if ontology is not None:
            set_meta_kv(conn, "ontology", json.dumps(ontology, sort_keys=True))
            set_meta_kv(conn, "ontology_hash", ontology_hash(ontology))
        if embedder_id is not None:
            set_meta_kv(conn, "embedder_id", embedder_id)
        if extractor_id is not None:
            set_meta_kv(conn, "extractor_id", extractor_id)
    finally:
        conn.close()


# ---------- file-level manifest ----------

def upsert_file(conn: sqlite3.Connection, rel_path: str, *, mtime: float,
                size: int, hash_: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO files (rel_path, mtime, size, content_hash, indexed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (rel_path, mtime, size, hash_, time.time()),
    )
    conn.commit()


def remove_file(conn: sqlite3.Connection, rel_path: str) -> None:
    conn.execute("DELETE FROM files WHERE rel_path = ?", (rel_path,))
    conn.commit()


def all_indexed_files(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Return ``{rel_path: {mtime, size, content_hash, indexed_at}}``."""
    rows = conn.execute(
        "SELECT rel_path, mtime, size, content_hash, indexed_at FROM files"
    ).fetchall()
    return {
        rp: {"mtime": m, "size": s, "content_hash": h, "indexed_at": ia}
        for rp, m, s, h, ia in rows
    }


def is_file_current(conn: sqlite3.Connection, rel_path: str, *,
                    mtime: float, hash_: str) -> bool:
    """Cheap: equal mtime → trust hash without recomputing. Otherwise re-hash."""
    row = conn.execute(
        "SELECT mtime, content_hash FROM files WHERE rel_path = ?", (rel_path,)
    ).fetchone()
    if row is None:
        return False
    saved_mtime, saved_hash = row
    if abs(saved_mtime - mtime) < 1e-3:
        return saved_hash == hash_ if hash_ else True
    return saved_hash == hash_
