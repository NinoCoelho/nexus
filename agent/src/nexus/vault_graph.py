"""vault_graph — build a file-link graph from the vault's markdown files.

Supports full-graph and scoped queries (file, folder, tag, search, entity).
Graph is cached with write-through invalidation.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, TypedDict

import yaml

from .vault import _vault_root

log = logging.getLogger(__name__)

_LINK_RE = re.compile(r'\]\((?:vault://)?([^)]+\.mdx?)\)')
_BARE_RE = re.compile(r'(?<!\()(?<!\])\b([\w./-]+/[\w./-]+\.mdx?)\b')

_CACHE_TTL = 60.0
_cache: tuple[float, Any] | None = None


def invalidate_cache() -> None:
    global _cache
    _cache = None


class GraphNode(TypedDict):
    path: str
    size: int
    folder: str
    tags: list[str]
    title: str


class GraphEdge(TypedDict):
    from_: str
    to: str


class ScopedGraphEdge(TypedDict):
    from_: str
    to: str
    type: str


class EntityNode(TypedDict):
    id: int
    name: str
    type: str
    source_paths: list[str]


class GraphData(TypedDict):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    orphans: list[str]


class ScopedGraphData(TypedDict):
    nodes: list[GraphNode]
    edges: list[ScopedGraphEdge]
    entity_nodes: list[EntityNode]
    orphans: list[str]


def _top_folder(rel: str) -> str:
    parts = Path(rel).parts
    return parts[0] if len(parts) > 1 else ""


def _parse_frontmatter(content: str) -> dict[str, Any] | None:
    if not content.startswith("---"):
        return None
    end = content.find("\n---", 3)
    if end == -1:
        return None
    fm_text = content[3:end].strip()
    try:
        fm = yaml.safe_load(fm_text)
        return fm if isinstance(fm, dict) else None
    except yaml.YAMLError:
        return None


def _extract_title(content: str) -> str:
    fm = _parse_frontmatter(content)
    if fm and isinstance(fm.get("title"), str):
        return fm["title"]
    first_line = content.lstrip().split("\n", 1)[0].strip()
    if first_line.startswith("# "):
        return first_line[2:].strip()
    return ""


def build_graph() -> GraphData:
    global _cache
    now = time.monotonic()
    if _cache is not None and now < _cache[0]:
        return _cache[1]

    result = _build_full()
    _cache = (now + _CACHE_TTL, result)
    return result


def _build_full() -> GraphData:
    from . import vault_index

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

    builders = {
        "file": _scope_file,
        "folder": _scope_folder,
        "tag": _scope_tag,
        "search": _scope_search,
        "entity": _scope_entity,
    }
    builder = builders.get(scope)
    if builder is None:
        full = build_graph()
        return ScopedGraphData(
            nodes=full["nodes"],
            edges=[ScopedGraphEdge(from_=e["from_"], to=e["to"], type="link") for e in full["edges"]],
            entity_nodes=[],
            orphans=full["orphans"],
        )
    return builder(seed=seed, hops=hops, edge_types=edge_types)


def _scope_file(*, seed: str, hops: int, edge_types: str) -> ScopedGraphData:
    full = build_graph()
    adj: dict[str, set[str]] = {}
    for e in full["edges"]:
        adj.setdefault(e["from_"], set()).add(e["to_"])
        adj.setdefault(e["to_"], set()).add(e["from_"])

    visited: set[str] = set()
    frontier: set[str] = {seed}
    for _ in range(hops):
        next_frontier: set[str] = set()
        for node in frontier:
            if node in visited:
                continue
            visited.add(node)
            for neighbor in adj.get(node, set()):
                if neighbor not in visited:
                    next_frontier.add(neighbor)
        frontier = next_frontier

    node_map = {n["path"]: n for n in full["nodes"]}
    scoped_nodes = [node_map[p] for p in visited if p in node_map]
    scoped_set = visited
    scoped_edges: list[ScopedGraphEdge] = []
    for e in full["edges"]:
        if e["from_"] in scoped_set and e["to_"] in scoped_set:
            scoped_edges.append(ScopedGraphEdge(from_=e["from_"], to=e["to_"], type="link"))

    connected: set[str] = set()
    for e in scoped_edges:
        connected.add(e["from_"])
        connected.add(e["to_"])
    orphans = [n["path"] for n in scoped_nodes if n["path"] not in connected]

    etypes = [t.strip() for t in edge_types.split(",") if t.strip()]
    entity_nodes: list[EntityNode] = []
    if "entity" in etypes:
        entity_nodes = _entity_nodes_for_paths(visited)

    if "tag" in etypes:
        scoped_edges.extend(_tag_cooccurrence_edges(scoped_nodes))

    return ScopedGraphData(
        nodes=scoped_nodes,
        edges=scoped_edges,
        entity_nodes=entity_nodes,
        orphans=orphans,
    )


def _scope_folder(*, seed: str, hops: int, edge_types: str) -> ScopedGraphData:
    full = build_graph()
    folder_prefix = seed if seed.endswith("/") else seed + "/"

    folder_nodes = [n for n in full["nodes"] if n["path"].startswith(folder_prefix)]
    folder_paths = {n["path"] for n in folder_nodes}

    scoped_edges: list[ScopedGraphEdge] = []
    for e in full["edges"]:
        src_in = e["from_"] in folder_paths
        dst_in = e["to_"] in folder_paths
        if src_in and dst_in:
            scoped_edges.append(ScopedGraphEdge(from_=e["from_"], to=e["to_"], type="link"))
        elif src_in or dst_in:
            scoped_edges.append(ScopedGraphEdge(from_=e["from_"], to=e["to_"], type="folder-cross"))

    if hops >= 2:
        adj: dict[str, set[str]] = {}
        for e in full["edges"]:
            adj.setdefault(e["from_"], set()).add(e["to_"])
            adj.setdefault(e["to_"], set()).add(e["from_"])
        expanded = set(folder_paths)
        frontier = set(folder_paths)
        for _ in range(hops - 1):
            next_f: set[str] = set()
            for p in frontier:
                for nb in adj.get(p, set()):
                    if nb not in expanded:
                        expanded.add(nb)
                        next_f.add(nb)
            frontier = next_f
        node_map = {n["path"]: n for n in full["nodes"]}
        for p in expanded - folder_paths:
            if p in node_map and p not in folder_paths:
                folder_nodes.append(node_map[p])
        for e in full["edges"]:
            if e["from_"] in expanded and e["to_"] in expanded:
                already = any(se["from_"] == e["from_"] and se["to_"] == e["to_"] for se in scoped_edges)
                if not already:
                    scoped_edges.append(ScopedGraphEdge(from_=e["from_"], to=e["to_"], type="link"))

    connected: set[str] = set()
    for e in scoped_edges:
        connected.add(e["from_"])
        connected.add(e["to_"])
    all_paths = {n["path"] for n in folder_nodes}
    orphans = [p for p in all_paths if p not in connected]

    etypes = [t.strip() for t in edge_types.split(",") if t.strip()]
    entity_nodes: list[EntityNode] = []
    if "entity" in etypes:
        entity_nodes = _entity_nodes_for_paths(all_paths)
    if "tag" in etypes:
        scoped_edges.extend(_tag_cooccurrence_edges(folder_nodes))

    return ScopedGraphData(
        nodes=folder_nodes,
        edges=scoped_edges,
        entity_nodes=entity_nodes,
        orphans=orphans,
    )


def _scope_tag(*, seed: str, hops: int, edge_types: str) -> ScopedGraphData:
    from . import vault_index

    if vault_index.is_empty():
        vault_index.rebuild_from_disk()
    tag_files = set(vault_index.files_with_tag(seed))

    full = build_graph()
    node_map = {n["path"]: n for n in full["nodes"]}
    tagged_nodes = [node_map[p] for p in tag_files if p in node_map]

    scoped_edges: list[ScopedGraphEdge] = []
    for e in full["edges"]:
        if e["from_"] in tag_files and e["to_"] in tag_files:
            scoped_edges.append(ScopedGraphEdge(from_=e["from_"], to=e["to_"], type="link"))

    scoped_edges.extend(_tag_cooccurrence_edges(tagged_nodes, shared_tag=seed))

    if hops >= 2:
        adj: dict[str, set[str]] = {}
        for e in full["edges"]:
            adj.setdefault(e["from_"], set()).add(e["to_"])
            adj.setdefault(e["to_"], set()).add(e["from_"])
        expanded = set(tag_files)
        frontier = set(tag_files)
        for _ in range(hops - 1):
            next_f: set[str] = set()
            for p in frontier:
                for nb in adj.get(p, set()):
                    if nb not in expanded:
                        expanded.add(nb)
                        next_f.add(nb)
            frontier = next_f
        for p in expanded - tag_files:
            if p in node_map:
                tagged_nodes.append(node_map[p])
        for e in full["edges"]:
            if e["from_"] in expanded and e["to_"] in expanded:
                already = any(se["from_"] == e["from_"] and se["to_"] == e["to_"] for se in scoped_edges)
                if not already:
                    scoped_edges.append(ScopedGraphEdge(from_=e["from_"], to=e["to_"], type="link"))

    connected: set[str] = set()
    for e in scoped_edges:
        connected.add(e["from_"])
        connected.add(e["to_"])
    all_paths = {n["path"] for n in tagged_nodes}
    orphans = [p for p in all_paths if p not in connected]

    etypes = [t.strip() for t in edge_types.split(",") if t.strip()]
    entity_nodes: list[EntityNode] = []
    if "entity" in etypes:
        entity_nodes = _entity_nodes_for_paths(all_paths)

    return ScopedGraphData(
        nodes=tagged_nodes,
        edges=scoped_edges,
        entity_nodes=entity_nodes,
        orphans=orphans,
    )


def _scope_search(*, seed: str, hops: int, edge_types: str) -> ScopedGraphData:
    from . import vault_search

    if vault_search.is_empty():
        vault_search.rebuild_from_disk()
    results = vault_search.search(seed, limit=50)
    result_paths = {r["path"] for r in results}

    full = build_graph()
    node_map = {n["path"]: n for n in full["nodes"]}
    matched_nodes = [node_map[p] for p in result_paths if p in node_map]

    scoped_edges: list[ScopedGraphEdge] = []
    for e in full["edges"]:
        if e["from_"] in result_paths and e["to_"] in result_paths:
            scoped_edges.append(ScopedGraphEdge(from_=e["from_"], to=e["to_"], type="link"))

    if hops >= 2:
        adj: dict[str, set[str]] = {}
        for e in full["edges"]:
            adj.setdefault(e["from_"], set()).add(e["to_"])
            adj.setdefault(e["to_"], set()).add(e["from_"])
        expanded = set(result_paths)
        frontier = set(result_paths)
        for _ in range(hops - 1):
            next_f: set[str] = set()
            for p in frontier:
                for nb in adj.get(p, set()):
                    if nb not in expanded:
                        expanded.add(nb)
                        next_f.add(nb)
            frontier = next_f
        for p in expanded - result_paths:
            if p in node_map:
                matched_nodes.append(node_map[p])
        for e in full["edges"]:
            if e["from_"] in expanded and e["to_"] in expanded:
                already = any(se["from_"] == e["from_"] and se["to_"] == e["to_"] for se in scoped_edges)
                if not already:
                    scoped_edges.append(ScopedGraphEdge(from_=e["from_"], to=e["to_"], type="link"))

    connected: set[str] = set()
    for e in scoped_edges:
        connected.add(e["from_"])
        connected.add(e["to_"])
    all_paths = {n["path"] for n in matched_nodes}
    orphans = [p for p in all_paths if p not in connected]

    etypes = [t.strip() for t in edge_types.split(",") if t.strip()]
    entity_nodes: list[EntityNode] = []
    if "entity" in etypes:
        entity_nodes = _entity_nodes_for_paths(all_paths)
    if "tag" in etypes:
        scoped_edges.extend(_tag_cooccurrence_edges(matched_nodes))

    return ScopedGraphData(
        nodes=matched_nodes,
        edges=scoped_edges,
        entity_nodes=entity_nodes,
        orphans=orphans,
    )


def _scope_entity(*, seed: str, hops: int, edge_types: str) -> ScopedGraphData:
    try:
        entity_id = int(seed)
    except ValueError:
        return ScopedGraphData(nodes=[], edges=[], entity_nodes=[], orphans=[])

    source_paths = _source_paths_for_entity(entity_id)
    if not source_paths:
        return ScopedGraphData(nodes=[], edges=[], entity_nodes=[], orphans=[])

    full = build_graph()
    node_map = {n["path"]: n for n in full["nodes"]}
    entity_file_nodes = [node_map[p] for p in source_paths if p in node_map]
    entity_file_paths = {n["path"] for n in entity_file_nodes}

    scoped_edges: list[ScopedGraphEdge] = []
    for e in full["edges"]:
        if e["from_"] in entity_file_paths and e["to_"] in entity_file_paths:
            scoped_edges.append(ScopedGraphEdge(from_=e["from_"], to=e["to_"], type="link"))

    if hops >= 2:
        adj: dict[str, set[str]] = {}
        for e in full["edges"]:
            adj.setdefault(e["from_"], set()).add(e["to_"])
            adj.setdefault(e["to_"], set()).add(e["from_"])
        expanded = set(entity_file_paths)
        frontier = set(entity_file_paths)
        for _ in range(hops - 1):
            next_f: set[str] = set()
            for p in frontier:
                for nb in adj.get(p, set()):
                    if nb not in expanded:
                        expanded.add(nb)
                        next_f.add(nb)
            frontier = next_f
        for p in expanded - entity_file_paths:
            if p in node_map:
                entity_file_nodes.append(node_map[p])
        for e in full["edges"]:
            if e["from_"] in expanded and e["to_"] in expanded:
                already = any(se["from_"] == e["from_"] and se["to_"] == e["to_"] for se in scoped_edges)
                if not already:
                    scoped_edges.append(ScopedGraphEdge(from_=e["from_"], to=e["to_"], type="link"))

    entity_nodes = _entity_nodes_for_paths(entity_file_paths)

    for e_node in entity_nodes:
        for sp in e_node["source_paths"]:
            if sp in entity_file_paths:
                scoped_edges.append(ScopedGraphEdge(from_=sp, to=f"entity:{e_node['id']}", type="shared-entity"))

    connected: set[str] = set()
    for e in scoped_edges:
        connected.add(e["from_"])
        connected.add(e["to_"])
    all_paths = {n["path"] for n in entity_file_nodes}
    orphans = [p for p in all_paths if p not in connected]

    return ScopedGraphData(
        nodes=entity_file_nodes,
        edges=scoped_edges,
        entity_nodes=entity_nodes,
        orphans=orphans,
    )


def _tag_cooccurrence_edges(
    nodes: list[GraphNode],
    shared_tag: str | None = None,
) -> list[ScopedGraphEdge]:
    edges: list[ScopedGraphEdge] = []
    paths_by_tag: dict[str, list[str]] = {}
    for n in nodes:
        for tag in n.get("tags", []):
            paths_by_tag.setdefault(tag, []).append(n["path"])

    for tag, paths in paths_by_tag.items():
        if shared_tag and tag == shared_tag:
            continue
        if len(paths) < 2:
            continue
        for i in range(len(paths)):
            for j in range(i + 1, len(paths)):
                edges.append(ScopedGraphEdge(from_=paths[i], to=paths[j], type="tag-cooccurrence"))
    return edges


def _build_entity_nodes() -> list[EntityNode]:
    from .agent.graphrag_manager import get_engine
    engine = get_engine()
    if engine is None:
        return []

    graph = engine._entity_graph
    entities = graph.list_entities(limit=500)
    entity_nodes: list[EntityNode] = []
    for e in entities:
        source_paths = _source_paths_for_entity(e.id)
        if source_paths:
            entity_nodes.append(EntityNode(
                id=e.id,
                name=e.name,
                type=e.type or "",
                source_paths=source_paths,
            ))
    return entity_nodes


def _entity_nodes_for_paths(paths: set[str]) -> list[EntityNode]:
    from .agent.graphrag_manager import get_engine, entities_for_source
    engine = get_engine()
    if engine is None:
        return []

    entity_map: dict[int, EntityNode] = {}
    for p in paths:
        entities = entities_for_source(p)
        for ent in entities:
            eid = ent["id"]
            if eid not in entity_map:
                entity_map[eid] = EntityNode(
                    id=eid,
                    name=ent["name"],
                    type=ent.get("type", ""),
                    source_paths=[p],
                )
            else:
                if p not in entity_map[eid]["source_paths"]:
                    entity_map[eid]["source_paths"].append(p)
    return list(entity_map.values())


def _source_paths_for_entity(entity_id: int) -> list[str]:
    from .agent.graphrag_manager import sources_for_entity
    return sources_for_entity(entity_id)
