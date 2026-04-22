"""vault_index — SQLite-backed tags and backlinks index for the vault.

Index lives at ~/.nexus/vault_meta.sqlite (separate from FTS index).
Thread-safe via a module-level Lock.

Tag sources (both are indexed):
  - Frontmatter:  `tags: [alpha, beta]`
  - Body hashtags: `#tagname` at word boundaries; code blocks are stripped first.

Link sources (shared with vault_graph.py logic):
  - Markdown link destinations: ](path/to/file.md) or ](vault://path.md)
  - Bare path mentions: has at least one slash, ends in .md/.mdx
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_META_PATH = Path("~/.nexus/vault_meta.sqlite").expanduser()
_VAULT_ROOT = Path("~/.nexus/vault").expanduser()
_lock = threading.Lock()

# ── Regex patterns ────────────────────────────────────────────────────────────

# Strip fenced code blocks before scanning for hashtags
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)

# Hashtag: word boundary, starts with letter/digit, 1-32 chars total
_HASHTAG_RE = re.compile(r"(?:^|\s)#([a-z0-9][a-z0-9_-]{0,31})\b", re.IGNORECASE | re.MULTILINE)

# Link patterns (mirrors vault_graph.py)
_LINK_RE = re.compile(r'\]\((?:vault://)?([^)]+\.mdx?)\)')
_BARE_RE = re.compile(r'(?<!\()(?<!\])\b([\w./-]+/[\w./-]+\.mdx?)\b')

_DDL = """
CREATE TABLE IF NOT EXISTS file_tags (
    path TEXT NOT NULL,
    tag  TEXT NOT NULL,
    PRIMARY KEY (path, tag)
);
CREATE INDEX IF NOT EXISTS idx_file_tags_tag ON file_tags(tag);

CREATE TABLE IF NOT EXISTS file_links (
    from_path TEXT NOT NULL,
    to_path   TEXT NOT NULL,
    PRIMARY KEY (from_path, to_path)
);
CREATE INDEX IF NOT EXISTS idx_file_links_to ON file_links(to_path);
"""


def _connect() -> sqlite3.Connection:
    _META_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(_META_PATH), check_same_thread=False)
    con.executescript(_DDL)
    con.commit()
    return con


def _norm_path(path: str) -> str:
    p = Path(path)
    if p.is_absolute():
        try:
            p = p.relative_to(_VAULT_ROOT.expanduser())
        except ValueError:
            pass
    return str(p)


# ── Extraction helpers ────────────────────────────────────────────────────────

def _extract_tags(body: str, frontmatter: dict[str, Any] | None) -> list[str]:
    """Extract tags from frontmatter `tags` list and `#hashtag` mentions in body."""
    tags: set[str] = set()

    # Frontmatter tags
    if frontmatter:
        fm_tags = frontmatter.get("tags")
        if isinstance(fm_tags, list):
            for t in fm_tags:
                if isinstance(t, str) and t:
                    tags.add(t.lower())
        elif isinstance(fm_tags, str) and fm_tags:
            tags.add(fm_tags.lower())

    # Body hashtags (strip code blocks first)
    stripped = _FENCE_RE.sub("", body or "")
    for m in _HASHTAG_RE.finditer(stripped):
        tags.add(m.group(1).lower())

    return sorted(tags)


def extract_links(body: str) -> set[str]:
    """Extract link destinations from markdown body (mirrors vault_graph.py logic)."""
    candidates: set[str] = set()
    for m in _LINK_RE.finditer(body):
        candidates.add(m.group(1))
    for m in _BARE_RE.finditer(body):
        candidates.add(m.group(1))
    return candidates


# ── Public API ────────────────────────────────────────────────────────────────

def reindex_file(path: str, body: str, frontmatter: dict[str, Any] | None) -> None:
    """Delete prior rows for path and re-insert tags + links."""
    norm = _norm_path(path)
    tags = _extract_tags(body or "", frontmatter)
    raw_links = extract_links(body or "")

    # Resolve link destinations to normalized paths
    vault_root = _VAULT_ROOT.expanduser()
    src_full = vault_root / norm
    link_paths: set[str] = set()
    for dest in raw_links:
        dest_norm = dest.lstrip("/")
        candidate = vault_root / dest_norm
        if candidate.is_file():
            link_paths.add(dest_norm)
        else:
            # Try relative to file's directory
            resolved = (src_full.parent / dest).resolve()
            if resolved.is_relative_to(vault_root) and resolved.is_file():
                try:
                    link_paths.add(str(resolved.relative_to(vault_root)))
                except ValueError:
                    pass

    with _lock:
        con = _connect()
        try:
            con.execute("DELETE FROM file_tags WHERE path = ?", (norm,))
            con.execute("DELETE FROM file_links WHERE from_path = ?", (norm,))
            for tag in tags:
                con.execute(
                    "INSERT OR IGNORE INTO file_tags(path, tag) VALUES (?, ?)", (norm, tag)
                )
            for to_path in link_paths:
                if to_path != norm:
                    con.execute(
                        "INSERT OR IGNORE INTO file_links(from_path, to_path) VALUES (?, ?)",
                        (norm, to_path),
                    )
            con.commit()
        finally:
            con.close()


def remove_file(path: str) -> None:
    """Remove all index rows for path."""
    norm = _norm_path(path)
    with _lock:
        con = _connect()
        try:
            con.execute("DELETE FROM file_tags WHERE path = ?", (norm,))
            con.execute("DELETE FROM file_links WHERE from_path = ?", (norm,))
            con.execute("DELETE FROM file_links WHERE to_path = ?", (norm,))
            con.commit()
        finally:
            con.close()


def rename_file(from_path: str, to_path: str) -> None:
    """Update all references when a file is renamed/moved."""
    from_norm = _norm_path(from_path)
    to_norm = _norm_path(to_path)
    with _lock:
        con = _connect()
        try:
            con.execute(
                "UPDATE file_tags SET path = ? WHERE path = ?", (to_norm, from_norm)
            )
            con.execute(
                "UPDATE file_links SET from_path = ? WHERE from_path = ?", (to_norm, from_norm)
            )
            con.execute(
                "UPDATE file_links SET to_path = ? WHERE to_path = ?", (to_norm, from_norm)
            )
            con.commit()
        finally:
            con.close()


def list_tags() -> list[dict]:
    """Return [{tag, count}] ordered by count desc."""
    with _lock:
        con = _connect()
        try:
            rows = con.execute(
                "SELECT tag, COUNT(*) as count FROM file_tags GROUP BY tag ORDER BY count DESC, tag ASC"
            ).fetchall()
        finally:
            con.close()
    return [{"tag": r[0], "count": r[1]} for r in rows]


def files_with_tag(tag: str) -> list[str]:
    """Return list of paths that have this tag."""
    with _lock:
        con = _connect()
        try:
            rows = con.execute(
                "SELECT path FROM file_tags WHERE tag = ? ORDER BY path", (tag.lower(),)
            ).fetchall()
        finally:
            con.close()
    return [r[0] for r in rows]


def backlinks(path: str) -> list[str]:
    """Return list of paths that link TO this file."""
    norm = _norm_path(path)
    with _lock:
        con = _connect()
        try:
            rows = con.execute(
                "SELECT from_path FROM file_links WHERE to_path = ? ORDER BY from_path", (norm,)
            ).fetchall()
        finally:
            con.close()
    return [r[0] for r in rows]


def forward_links(path: str) -> list[str]:
    """Return list of paths that this file links TO."""
    norm = _norm_path(path)
    with _lock:
        con = _connect()
        try:
            rows = con.execute(
                "SELECT to_path FROM file_links WHERE from_path = ? ORDER BY to_path", (norm,)
            ).fetchall()
        finally:
            con.close()
    return [r[0] for r in rows]


def all_links() -> list[tuple[str, str]]:
    """Return all (from_path, to_path) link pairs."""
    with _lock:
        con = _connect()
        try:
            rows = con.execute(
                "SELECT from_path, to_path FROM file_links ORDER BY from_path, to_path"
            ).fetchall()
        finally:
            con.close()
    return [(r[0], r[1]) for r in rows]


def tags_for_file(path: str) -> list[str]:
    """Return tags for a specific file."""
    norm = _norm_path(path)
    with _lock:
        con = _connect()
        try:
            rows = con.execute(
                "SELECT tag FROM file_tags WHERE path = ? ORDER BY tag", (norm,)
            ).fetchall()
        finally:
            con.close()
    return [r[0] for r in rows]


def is_empty() -> bool:
    """Return True if both tables are empty."""
    with _lock:
        con = _connect()
        try:
            row = con.execute("SELECT COUNT(*) FROM file_tags").fetchone()
            return (row[0] if row else 0) == 0
        finally:
            con.close()


def rebuild_from_disk() -> int:
    """Re-index all .md/.mdx files under the vault root. Returns file count."""
    import yaml

    vault_root = _VAULT_ROOT.expanduser()
    vault_root.mkdir(parents=True, exist_ok=True)

    files = list(vault_root.rglob("*.md")) + list(vault_root.rglob("*.mdx"))

    with _lock:
        con = _connect()
        try:
            con.execute("DELETE FROM file_tags")
            con.execute("DELETE FROM file_links")
            count = 0
            for fp in files:
                rel_parts = fp.relative_to(vault_root).parts
                if any(part.startswith(".") for part in rel_parts):
                    continue
                try:
                    content = fp.read_text(encoding="utf-8", errors="replace")
                    rel = str(fp.relative_to(vault_root))

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

                    # Tags
                    tags = _extract_tags(body, fm)
                    for tag in tags:
                        con.execute(
                            "INSERT OR IGNORE INTO file_tags(path, tag) VALUES (?, ?)", (rel, tag)
                        )

                    # Links (resolve against vault root)
                    raw_links = extract_links(body)
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
                    count += 1
                except OSError as exc:
                    log.warning("vault_index: skipping %s: %s", fp, exc)
            con.commit()
        finally:
            con.close()
    return count
