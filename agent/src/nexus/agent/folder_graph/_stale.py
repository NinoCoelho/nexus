"""Stale-file detection: compare current folder mtimes vs the manifest."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ._scanner import iter_indexable_files
from ._storage import all_indexed_files, folder_dot_dir, normalize_folder


def stale_files(folder: str | Path) -> dict[str, list[str]]:
    """Walk the folder, compare against manifest, return diff.

    Returns ``{"added": [...], "changed": [...], "removed": [...]}`` with
    folder-relative POSIX paths. ``added`` and ``changed`` rely on mtime as
    a fast first check; both fall back to size to avoid forcing a re-hash
    for files mtime didn't tick. Hash check only happens during indexing.
    """
    folder_p = normalize_folder(folder)
    if not (folder_dot_dir(folder_p) / "manifest.sqlite").is_file():
        # No index yet — every file is "added"
        return {
            "added": [rp for rp, _, _, _ in iter_indexable_files(folder_p)],
            "changed": [],
            "removed": [],
        }

    conn = sqlite3.connect(str(folder_dot_dir(folder_p) / "manifest.sqlite"))
    try:
        indexed = all_indexed_files(conn)
    finally:
        conn.close()

    on_disk: dict[str, dict[str, Any]] = {}
    for rel_path, abs_path, mtime, size in iter_indexable_files(folder_p):
        on_disk[rel_path] = {"mtime": mtime, "size": size}

    added: list[str] = []
    changed: list[str] = []
    for rel_path, info in on_disk.items():
        prev = indexed.get(rel_path)
        if prev is None:
            added.append(rel_path)
            continue
        if abs(prev["mtime"] - info["mtime"]) > 1e-3 or prev["size"] != info["size"]:
            changed.append(rel_path)

    removed = [rp for rp in indexed.keys() if rp not in on_disk]
    return {"added": sorted(added), "changed": sorted(changed), "removed": sorted(removed)}
