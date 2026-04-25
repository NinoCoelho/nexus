"""vault_index_rebuild — disk-scan re-indexer for vault_index.

Extracted from vault_index.py to keep that module under 300 LOC.
The public entry point is :func:`rebuild_from_disk`; call it via
``vault_index.rebuild_from_disk()`` (re-exported from there).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def rebuild_from_disk(
    *,
    vault_root: Path,
    connect_fn: Any,
    lock: Any,
    extract_tags_fn: Any,
    extract_links_fn: Any,
    full: bool = False,
) -> int:
    """Re-index .md/.mdx files under vault_root. Returns file count.

    Parameters are injected by vault_index to avoid circular imports and
    to keep this module free of module-level state.

    With ``full=False`` (default), uses ``file_meta(mtime, size)`` to skip
    files that haven't changed since last index, and prunes rows for files
    that no longer exist on disk. With ``full=True``, deletes everything
    and rebuilds from scratch.
    """
    import yaml

    vault_root.mkdir(parents=True, exist_ok=True)
    files = list(vault_root.rglob("*.md")) + list(vault_root.rglob("*.mdx"))

    with lock:
        con = connect_fn()
        try:
            if full:
                con.execute("DELETE FROM file_tags")
                con.execute("DELETE FROM file_links")
                con.execute("DELETE FROM file_meta")

            existing: dict[str, tuple[float, int]] = {}
            if not full:
                for row in con.execute("SELECT path, mtime, size FROM file_meta"):
                    existing[row[0]] = (row[1], row[2])

            disk_paths: set[str] = set()
            count = 0
            for fp in files:
                rel_parts = fp.relative_to(vault_root).parts
                if any(part.startswith(".") for part in rel_parts):
                    continue
                try:
                    rel = str(fp.relative_to(vault_root))
                    disk_paths.add(rel)
                    st = fp.stat()
                    if not full:
                        prev = existing.get(rel)
                        if prev is not None and prev[0] == st.st_mtime and prev[1] == st.st_size:
                            count += 1
                            continue

                    content = fp.read_text(encoding="utf-8", errors="replace")

                    # Parse frontmatter
                    fm: dict[str, Any] | None = None
                    body = content
                    if content.startswith("---"):
                        end = content.find("\n---", 3)
                        if end != -1:
                            fm_text = content[3:end].strip()
                            body = content[end + 4:].lstrip("\n")
                            try:
                                parsed = yaml.safe_load(fm_text)
                                fm = parsed if isinstance(parsed, dict) else None
                            except yaml.YAMLError:
                                pass

                    # Replace this file's rows
                    con.execute("DELETE FROM file_tags WHERE path = ?", (rel,))
                    con.execute("DELETE FROM file_links WHERE from_path = ?", (rel,))

                    tags = extract_tags_fn(body, fm)
                    for tag in tags:
                        con.execute(
                            "INSERT OR IGNORE INTO file_tags(path, tag) VALUES (?, ?)",
                            (rel, tag),
                        )

                    raw_links = extract_links_fn(body)
                    src_full = vault_root / rel
                    for dest in raw_links:
                        dest_norm = dest.lstrip("/")
                        candidate = vault_root / dest_norm
                        to_path: str | None = None
                        if candidate.is_file():
                            to_path = dest_norm
                        else:
                            resolved = (src_full.parent / dest).resolve()
                            if resolved.is_relative_to(vault_root) and resolved.is_file():
                                try:
                                    to_path = str(resolved.relative_to(vault_root))
                                except ValueError:
                                    pass
                        if to_path and to_path != rel:
                            con.execute(
                                "INSERT OR IGNORE INTO file_links(from_path, to_path) VALUES (?, ?)",
                                (rel, to_path),
                            )

                    con.execute(
                        "INSERT OR REPLACE INTO file_meta(path, mtime, size) VALUES (?, ?, ?)",
                        (rel, st.st_mtime, st.st_size),
                    )
                    count += 1
                except OSError as exc:
                    log.warning("vault_index: skipping %s: %s", fp, exc)

            if not full:
                stale = set(existing.keys()) - disk_paths
                for rel in stale:
                    con.execute("DELETE FROM file_tags WHERE path = ?", (rel,))
                    con.execute("DELETE FROM file_links WHERE from_path = ?", (rel,))
                    con.execute("DELETE FROM file_links WHERE to_path = ?", (rel,))
                    con.execute("DELETE FROM file_meta WHERE path = ?", (rel,))

            con.commit()
        finally:
            con.close()
    return count
