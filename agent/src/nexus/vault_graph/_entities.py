"""Entity node helper functions for vault_graph scoped queries."""

from __future__ import annotations

from .types import EntityNode


def _entity_nodes_for_paths(paths: set[str]) -> list[EntityNode]:
    from nexus.agent.graphrag_manager import get_engine, entities_for_source
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
    from nexus.agent.graphrag_manager import sources_for_entity
    return sources_for_entity(entity_id)


def _build_entity_nodes() -> list[EntityNode]:
    from nexus.agent.graphrag_manager import get_engine
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
