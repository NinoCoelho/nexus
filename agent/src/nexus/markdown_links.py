"""Shared markdown note-link extraction.

Single source of truth for link parsing across the vault link graph
(``vault_graph``) and the SQLite tag/link index (``vault_index``) so the two
can never drift again.

Supported forms (anchors stripped, aliases ignored):

  [text](note.md)           markdown link — relative or vault-absolute
  [text](note.md#section)   anchored markdown link
  [text](vault://note.md)   UI-scheme links
  [[note]]                  wiki-link — extension optional
  [[folder/note|Alias]]     wiki-link with alias
  [[note#heading|Alias]]    wiki-link with anchor + alias
  path/like.md              bare path mention (multi-segment only)

Links inside fenced code blocks are ignored.
"""

from __future__ import annotations

import re
from pathlib import Path

# Fenced code blocks: ``` or ~~~ fences, spanning to the matching close.
_FENCE_RE = re.compile(r"(?:```|~~~).*?(?:```|~~~)", re.DOTALL)

# [text](path.md) / [text](path.md#anchor) / [text](vault://path.md)
_MD_LINK_RE = re.compile(r"\]\((?:vault://)?([^()\s#]+\.mdx?)(?:#[^()\s]*)?\)")

# [[target]] / [[target#anchor]] / [[target|alias]] / [[target#anchor|alias]]
# — target may be a path with or without .md extension.
_WIKI_RE = re.compile(r"\[\[\s*([^\]|#]+?)\s*(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")

# Bare multi-segment path mentions, excluding ones already inside ( or [
_BARE_RE = re.compile(r"(?<!\()(?<!\])\b([\w./-]+/[\w./-]+\.mdx?)\b")


def strip_code_fences(text: str) -> str:
    """Remove fenced code blocks so their contents are never link-indexed."""
    return _FENCE_RE.sub("", text or "")


def extract_md_links(body: str, *, strip_fences: bool = True) -> set[str]:
    """Extract raw note-link targets from a markdown body.

    Returns unresolved target strings (anchors stripped, ``vault://`` scheme
    stripped). Callers resolve against their vault view — see
    :func:`resolve_targets`.
    """
    text = strip_code_fences(body) if strip_fences else (body or "")
    if not text:
        return set()

    candidates: set[str] = set()
    for m in _MD_LINK_RE.finditer(text):
        candidates.add(m.group(1))
    for m in _WIKI_RE.finditer(text):
        candidates.add(m.group(1))
    for m in _BARE_RE.finditer(text):
        candidates.add(m.group(1))
    return candidates


def resolve_targets(
    candidates: set[str],
    *,
    src_rel: str,
    path_set: set[str],
    stem_map: dict[str, list[str]] | None = None,
) -> set[str]:
    """Resolve raw link targets to vault-relative paths.

    Resolution order (first hit wins, per candidate):
      1. exact path match (leading ``/`` and ``./`` normalized)
      2. candidate + ``.md`` / ``.mdx`` when the target has no extension
         (``[[note]]`` → ``note.md``)
      3. relative to the source file's folder
      4. unique stem match anywhere in the vault (Obsidian-style basename
         resolution; requires ``stem_map`` — ambiguous stems resolve to the
         alphabetically first path)

    Returns only targets that exist in ``path_set``, excluding self-links.
    """
    if stem_map is None:
        stem_map = {}
        for p in path_set:
            stem_map.setdefault(Path(p).stem, []).append(p)
        for v in stem_map.values():
            v.sort()

    src_dir = str(Path(src_rel).parent)
    src_dir = "" if src_dir == "." else src_dir

    def _try(norm: str) -> str | None:
        if norm in path_set:
            return norm
        return None

    resolved: set[str] = set()
    for cand in candidates:
        norm = cand.strip().lstrip("/").removeprefix("./")
        if not norm:
            continue
        hit = _try(norm)
        if hit is None and not norm.endswith((".md", ".mdx")):
            for ext in (".md", ".mdx"):
                hit = _try(norm + ext)
                if hit is not None:
                    break
        if hit is None and "/" in norm:
            rel = str(Path(src_dir) / norm) if src_dir else norm
            hit = _try(str(Path(rel)))
        if hit is None:
            stem = Path(norm).stem
            matches = stem_map.get(stem)
            if matches:
                hit = matches[0]
        if hit is not None and hit != src_rel:
            resolved.add(hit)
    return resolved
