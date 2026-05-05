"""Read-side helpers around a per-folder GraphRAGEngine.

Mirrors the slice of ``graphrag_manager._indexer`` that the global
knowledge graph endpoints use, parameterised on a per-folder engine.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def full_subgraph(engine: Any, *, max_nodes: int = 500,
                  ontology: dict[str, Any] | None = None) -> dict[str, Any]:
    """All entities + edges in the folder DB. Capped to keep payloads sane."""
    if engine is None:
        return {"nodes": [], "edges": [], "ontology": ontology or {}}
    result: dict[str, Any] = {"ontology": ontology or {}}
    graph = engine._entity_graph
    entities = graph.list_all_entities()
    if max_nodes and len(entities) > max_nodes:
        # Largest-degree first so the cap shows the most connected piece.
        ranked = sorted(entities, key=lambda e: graph.entity_degree(e.id), reverse=True)
        entities = ranked[:max_nodes]
        keep_ids: set[int] = {e.id for e in entities}
    else:
        keep_ids = {e.id for e in entities}

    nodes = [
        {"id": e.id, "name": e.name, "type": e.type or "",
         "degree": graph.entity_degree(e.id)}
        for e in entities
    ]
    edges: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()
    for t in graph.list_all_triples():
        if t.head_id not in keep_ids or t.tail_id not in keep_ids:
            continue
        key = (min(t.head_id, t.tail_id), max(t.head_id, t.tail_id), t.relation)
        if key in seen:
            continue
        seen.add(key)
        edges.append({
            "source": t.head_id,
            "target": t.tail_id,
            "relation": t.relation,
            "strength": t.strength,
        })
    result["nodes"] = nodes
    result["edges"] = edges
    return result


def subgraph_for_seed(engine: Any, seed: int, *, hops: int = 2,
                      width: int = 50) -> dict[str, Any]:
    if engine is None:
        return {"nodes": [], "edges": []}
    result = engine._entity_graph.subgraph(seed, max_hops=hops,
                                           max_neighbors_per_node=width)
    edge_map: dict[tuple[int, str, int], dict[str, Any]] = {}
    for e in result["edges"]:
        key = (e.head_id, e.relation, e.tail_id)
        existing = edge_map.get(key)
        if existing is None or e.strength > existing["strength"]:
            edge_map[key] = {
                "source": e.head_id,
                "target": e.tail_id,
                "relation": e.relation,
                "strength": e.strength,
            }
    return {
        "nodes": [
            {"id": n.id, "name": n.name, "type": n.type or "",
             "degree": engine._entity_graph.entity_degree(n.id)}
            for n in result["nodes"]
        ],
        "edges": list(edge_map.values()),
    }


async def query(engine: Any, query_text: str, *, limit: int = 10) -> dict[str, Any]:
    if engine is None or not query_text.strip():
        return {"results": [], "subgraph": {"nodes": [], "edges": []}}
    enriched = await engine.retrieve_enriched(query_text, top_k=limit)
    return {
        "results": [
            {
                "chunk_id": r.chunk_id,
                "source_path": r.source_path,
                "heading": r.heading,
                "content": r.content,
                "score": round(r.score, 4),
                "source": r.source,
                "related_entities": r.related_entities,
            }
            for r in enriched.results
        ],
        "subgraph": {
            "nodes": enriched.subgraph_nodes,
            "edges": enriched.subgraph_edges,
        },
    }
