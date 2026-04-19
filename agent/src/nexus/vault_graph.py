"""vault_graph — build a file-link graph from the vault's markdown files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TypedDict

from .vault import _vault_root

# Patterns for extracting link destinations from markdown
# 1. Markdown link syntax: ](vault://path) or ](path/to/file.md)
_LINK_RE = re.compile(r'\]\((?:vault://)?([^)]+\.mdx?)\)')
# 2. Bare path mentions: has at least one slash, ends in .md/.mdx
_BARE_RE = re.compile(r'(?<!\()(?<!\])\b([\w./-]+/[\w./-]+\.mdx?)\b')


class GraphNode(TypedDict):
    path: str
    size: int
    folder: str


class GraphEdge(TypedDict):
    from_: str
    to: str


class GraphData(TypedDict):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    orphans: list[str]


def _top_folder(rel: str) -> str:
    """Return top-level directory, or '' for root-level files."""
    parts = Path(rel).parts
    return parts[0] if len(parts) > 1 else ""


def build_graph() -> GraphData:
    root = _vault_root()
    root_real = Path(root).resolve()

    # Collect all .md/.mdx files
    md_files: list[Path] = []
    for p in root_real.rglob("*"):
        if p.is_file() and p.suffix in (".md", ".mdx"):
            # Skip hidden files/dirs
            rel_parts = p.relative_to(root_real).parts
            if any(part.startswith(".") for part in rel_parts):
                continue
            md_files.append(p)

    # Build path set for fast membership testing (relative paths)
    path_set: set[str] = {str(p.relative_to(root_real)) for p in md_files}

    nodes: list[GraphNode] = []
    for p in sorted(md_files):
        rel = str(p.relative_to(root_real))
        size = p.stat().st_size
        nodes.append(GraphNode(path=rel, size=size, folder=_top_folder(rel)))

    # Extract edges
    edges_set: set[tuple[str, str]] = set()
    for p in md_files:
        src_rel = str(p.relative_to(root_real))
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        candidates: set[str] = set()
        for m in _LINK_RE.finditer(content):
            candidates.add(m.group(1))
        for m in _BARE_RE.finditer(content):
            candidates.add(m.group(1))

        for dest in candidates:
            # Normalize: try as-is, then relative to file's directory
            dest_path = dest.lstrip("/")
            if dest_path in path_set:
                key = (src_rel, dest_path)
                if key[0] != key[1]:
                    edges_set.add(key)
            else:
                # Try resolving relative to the source file's directory
                resolved = str((p.parent / dest).resolve().relative_to(root_real)) if (p.parent / dest).resolve().is_relative_to(root_real) else None
                if resolved and resolved in path_set and src_rel != resolved:
                    edges_set.add((src_rel, resolved))

    edges: list[GraphEdge] = [GraphEdge(from_=f, to=t) for f, t in sorted(edges_set)]

    # Compute orphans: nodes with no edges
    connected: set[str] = set()
    for f, t in edges_set:
        connected.add(f)
        connected.add(t)
    orphans = [n["path"] for n in nodes if n["path"] not in connected]

    return GraphData(nodes=nodes, edges=edges, orphans=orphans)
