"""vault_graph — build a file-link graph from the vault's markdown files.

Supports full-graph and scoped queries (file, folder, tag, search, entity).
Graph is cached with write-through invalidation.
"""

from .builder import build_graph, build_scoped_graph
from .cache import invalidate_cache
from .types import (
    EntityNode,
    GraphData,
    GraphEdge,
    GraphNode,
    ScopedGraphData,
    ScopedGraphEdge,
)

__all__ = [
    "build_graph",
    "build_scoped_graph",
    "invalidate_cache",
    "EntityNode",
    "GraphData",
    "GraphEdge",
    "GraphNode",
    "ScopedGraphData",
    "ScopedGraphEdge",
]
