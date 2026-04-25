"""Full graph building functions for vault_graph."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .cache import get_cache, set_cache
from .parser import _extract_title, _top_folder
from ._entities import _build_entity_nodes
from .scoped import (
    scope_entity,
    scope_file,
    scope_folder,
    scope_search,
    scope_tag,
)
from .types import (
    GraphData,
    GraphEdge,
    GraphNode,
    ScopedGraphData,
    ScopedGraphEdge,
)

log = logging.getLogger(__name__)

_LINK_RE = re.compile(r'\]\((?:vault://)?([^)]+\.mdx?)\)')
_BARE_RE = re.compile(r'(?<!\()(?<!\])\b([\w./-]+/[\w./-]+\.mdx?)\b')


def build_graph() -> GraphData:
    cached = get_cache()
    if cached is not None:
        return cached

    result = _build_full()
    set_cache(result)
    return result


def _build_full() -> GraphData:
    from nexus.vault import _vault_root
    from nexus import vault_index

    root = _vault_root()
    root_real = Path(root).resolve()

    md_files: list[Path] = []
    for p in root_real.rglob("*"):
        if p.is_file() and p.suffix in (".md", ".mdx"):
            rel_parts = p.relative_to(root_real).parts
            if any(part.startswith(".") for part in rel_parts):
                continue
            md_files.append(p)

    path_set: set[str] = {str(p.relative_to(root_real)) for p in md_files}

    tag_map: dict[str, list[str]] = {}
    try:
        if vault_index.is_empty():
            vault_index.rebuild_from_disk()
        for row in vault_index.list_tags():
            pass
        for p_str in path_set:
            tag_map[p_str] = vault_index.tags_for_file(p_str)
    except Exception:
        log.warning("vault_graph: tag enrichment failed", exc_info=True)

    nodes: list[GraphNode] = []
    for p in sorted(md_files):
        rel = str(p.relative_to(root_real))
        size = p.stat().st_size
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
            title = _extract_title(content)
        except OSError:
            title = ""
        nodes.append(GraphNode(
            path=rel,
            size=size,
            folder=_top_folder(rel),
            tags=tag_map.get(rel, []),
            title=title,
        ))

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
            dest_path = dest.lstrip("/")
            if dest_path in path_set:
                key = (src_rel, dest_path)
                if key[0] != key[1]:
                    edges_set.add(key)
            else:
                resolved = str((p.parent / dest).resolve().relative_to(root_real)) if (p.parent / dest).resolve().is_relative_to(root_real) else None
                if resolved and resolved in path_set and src_rel != resolved:
                    edges_set.add((src_rel, resolved))

    edges: list[GraphEdge] = [GraphEdge(from_=f, to=t) for f, t in sorted(edges_set)]

    connected: set[str] = set()
    for f, t in edges_set:
        connected.add(f)
        connected.add(t)
    orphans = [n["path"] for n in nodes if n["path"] not in connected]

    return GraphData(nodes=nodes, edges=edges, orphans=orphans)


def build_scoped_graph(
    *,
    scope: str = "all",
    seed: str = "",
    hops: int = 1,
    edge_types: str = "link",
) -> ScopedGraphData:
    if scope == "all" or not seed:
        full = build_graph()
        etypes = [e.strip() for e in edge_types.split(",") if e.strip()] if edge_types else ["link"]
        scoped_edges: list[ScopedGraphEdge] = []
        for e in full["edges"]:
            scoped_edges.append(ScopedGraphEdge(from_=e["from_"], to=e["to"], type="link"))
        entity_nodes = _build_entity_nodes() if "entity" in etypes else []
        return ScopedGraphData(
            nodes=full["nodes"],
            edges=scoped_edges,
            entity_nodes=entity_nodes,
            orphans=full["orphans"],
        )

    full = build_graph()
    builders = {
        "file": scope_file,
        "folder": scope_folder,
        "tag": scope_tag,
        "search": scope_search,
        "entity": scope_entity,
    }
    builder = builders.get(scope)
    if builder is None:
        return ScopedGraphData(
            nodes=full["nodes"],
            edges=[ScopedGraphEdge(from_=e["from_"], to=e["to"], type="link") for e in full["edges"]],
            entity_nodes=[],
            orphans=full["orphans"],
        )
    return builder(seed=seed, hops=hops, edge_types=edge_types, full=full)
