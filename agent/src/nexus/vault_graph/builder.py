"""Full graph building functions for vault_graph."""

from __future__ import annotations

import logging
from pathlib import Path

from nexus.markdown_links import extract_md_links, resolve_targets

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
    stem_map: dict[str, list[str]] = {}
    for p in path_set:
        stem_map.setdefault(Path(p).stem, []).append(p)
    for v in stem_map.values():
        v.sort()

    tag_map: dict[str, list[str]] = {}
    try:
        vault_index.ensure_ready()
        for p_str in path_set:
            tag_map[p_str] = vault_index.tags_for_file(p_str)
    except Exception:
        log.warning("vault_graph: tag enrichment failed", exc_info=True)

    nodes: list[GraphNode] = []
    contents: dict[str, str] = {}
    for p in sorted(md_files):
        rel = str(p.relative_to(root_real))
        size = p.stat().st_size
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            content = ""
        contents[rel] = content
        nodes.append(GraphNode(
            path=rel,
            size=size,
            folder=_top_folder(rel),
            tags=tag_map.get(rel, []),
            title=_extract_title(content),
        ))

    edges_set: set[tuple[str, str]] = set()
    for src_rel, content in contents.items():
        candidates = extract_md_links(content)
        for dest in resolve_targets(
            candidates, src_rel=src_rel, path_set=path_set, stem_map=stem_map
        ):
            edges_set.add((src_rel, dest))

    edges: list[GraphEdge] = [GraphEdge(from_=f, to=t) for f, t in sorted(edges_set)]

    degree: dict[str, int] = {}
    for f, t in edges_set:
        degree[f] = degree.get(f, 0) + 1
        degree[t] = degree.get(t, 0) + 1
    for n in nodes:
        n["degree"] = degree.get(n["path"], 0)

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
