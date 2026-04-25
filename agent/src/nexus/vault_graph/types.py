"""TypedDict types for vault_graph."""

from __future__ import annotations

from typing import TypedDict


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
