"""Scoped graph query functions for vault_graph."""

from __future__ import annotations

import logging
from typing import Any

from ._entities import _entity_nodes_for_paths, _source_paths_for_entity
from .types import (
    EntityNode,
    GraphNode,
    ScopedGraphData,
    ScopedGraphEdge,
)

log = logging.getLogger(__name__)


def _expand_hops(
    seed_paths: set[str],
    full_edges: list[Any],
    hops: int,
) -> set[str]:
    """BFS expansion from seed_paths over full_edges for (hops - 1) additional hops."""
    adj: dict[str, set[str]] = {}
    for e in full_edges:
        adj.setdefault(e["from_"], set()).add(e["to_"])
        adj.setdefault(e["to_"], set()).add(e["from_"])

    expanded = set(seed_paths)
    frontier = set(seed_paths)
    for _ in range(hops - 1):
        next_f: set[str] = set()
        for p in frontier:
            for nb in adj.get(p, set()):
                if nb not in expanded:
                    expanded.add(nb)
                    next_f.add(nb)
        frontier = next_f
    return expanded


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


def scope_file(*, seed: str, hops: int, edge_types: str, full: Any) -> ScopedGraphData:
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

    return ScopedGraphData(nodes=scoped_nodes, edges=scoped_edges, entity_nodes=entity_nodes, orphans=orphans)


def scope_folder(*, seed: str, hops: int, edge_types: str, full: Any) -> ScopedGraphData:
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
        expanded = _expand_hops(folder_paths, full["edges"], hops)
        node_map = {n["path"]: n for n in full["nodes"]}
        for p in expanded - folder_paths:
            if p in node_map:
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

    return ScopedGraphData(nodes=folder_nodes, edges=scoped_edges, entity_nodes=entity_nodes, orphans=orphans)


def scope_tag(*, seed: str, hops: int, edge_types: str, full: Any) -> ScopedGraphData:
    from nexus import vault_index

    if vault_index.is_empty():
        vault_index.rebuild_from_disk()
    tag_files = set(vault_index.files_with_tag(seed))

    node_map = {n["path"]: n for n in full["nodes"]}
    tagged_nodes = [node_map[p] for p in tag_files if p in node_map]

    scoped_edges: list[ScopedGraphEdge] = []
    for e in full["edges"]:
        if e["from_"] in tag_files and e["to_"] in tag_files:
            scoped_edges.append(ScopedGraphEdge(from_=e["from_"], to=e["to_"], type="link"))

    scoped_edges.extend(_tag_cooccurrence_edges(tagged_nodes, shared_tag=seed))

    if hops >= 2:
        expanded = _expand_hops(tag_files, full["edges"], hops)
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

    return ScopedGraphData(nodes=tagged_nodes, edges=scoped_edges, entity_nodes=entity_nodes, orphans=orphans)


def scope_search(*, seed: str, hops: int, edge_types: str, full: Any) -> ScopedGraphData:
    from nexus import vault_search

    if vault_search.is_empty():
        vault_search.rebuild_from_disk()
    results = vault_search.search(seed, limit=50)
    result_paths = {r["path"] for r in results}

    node_map = {n["path"]: n for n in full["nodes"]}
    matched_nodes = [node_map[p] for p in result_paths if p in node_map]

    scoped_edges: list[ScopedGraphEdge] = []
    for e in full["edges"]:
        if e["from_"] in result_paths and e["to_"] in result_paths:
            scoped_edges.append(ScopedGraphEdge(from_=e["from_"], to=e["to_"], type="link"))

    if hops >= 2:
        expanded = _expand_hops(result_paths, full["edges"], hops)
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

    return ScopedGraphData(nodes=matched_nodes, edges=scoped_edges, entity_nodes=entity_nodes, orphans=orphans)


def scope_entity(*, seed: str, hops: int, edge_types: str, full: Any) -> ScopedGraphData:
    try:
        entity_id = int(seed)
    except ValueError:
        return ScopedGraphData(nodes=[], edges=[], entity_nodes=[], orphans=[])

    source_paths = _source_paths_for_entity(entity_id)
    if not source_paths:
        return ScopedGraphData(nodes=[], edges=[], entity_nodes=[], orphans=[])

    node_map = {n["path"]: n for n in full["nodes"]}
    entity_file_nodes = [node_map[p] for p in source_paths if p in node_map]
    entity_file_paths = {n["path"] for n in entity_file_nodes}

    scoped_edges: list[ScopedGraphEdge] = []
    for e in full["edges"]:
        if e["from_"] in entity_file_paths and e["to_"] in entity_file_paths:
            scoped_edges.append(ScopedGraphEdge(from_=e["from_"], to=e["to_"], type="link"))

    if hops >= 2:
        expanded = _expand_hops(entity_file_paths, full["edges"], hops)
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

    return ScopedGraphData(nodes=entity_file_nodes, edges=scoped_edges, entity_nodes=entity_nodes, orphans=orphans)
