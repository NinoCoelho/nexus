"""Vault indexing functions (streaming + entity queries) for GraphRAG."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Iterator

from ._manifest import is_indexed, mark_indexed

log = logging.getLogger(__name__)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def index_vault_streaming(
    engine: Any,
    cfg: Any,
    *,
    full: bool = False,
    initialize_fn: Any,
    drop_data_fn: Any,
) -> Iterator[str]:
    """Yield SSE frames while indexing the vault into GraphRAG.

    When ``full=True``, drops all data first and reindexes every file.
    When ``full=False`` (default), skips files whose content hash hasn't
    changed since the last successful index (incremental).

    Each frame is ``event: <type>\\ndata: <json>\\n\\n``.
    Event types: ``status``, ``file``, ``error``, ``stats``, ``done``.
    """
    if full:
        yield _sse("status", {"message": "Dropping existing index…"})
        drop_data_fn()
        yield _sse("status", {"message": "Reinitializing engine…"})
        await initialize_fn(cfg)
        # engine ref may have changed — caller must re-fetch
        from nexus.agent.graphrag_manager import get_engine
        engine = get_engine()

    if engine is None:
        yield _sse("error", {"detail": "GraphRAG engine not initialized"})
        return

    yield _sse("status", {"message": "Scanning vault files…"})

    from nexus import vault
    entries = vault.list_tree()
    files = [e for e in entries if e.type == "file"]
    total = len(files)
    label = "full reindex" if full else "incremental update"
    yield _sse("status", {"message": f"Found {total} vault file(s) — {label}"})

    files_done = 0
    files_skipped = 0
    files_indexed = 0
    entities_before = engine._entity_graph.count_entities()
    triples_before = engine._entity_graph.count_triples()
    t0 = time.monotonic()

    for entry in files:
        try:
            result = vault.read_file(entry.path)
            content = result.get("content", "")
            if not content:
                files_done += 1
                continue
            if not full and is_indexed(entry.path, content):
                files_skipped += 1
                files_done += 1
                yield _sse("file", {
                    "path": entry.path,
                    "files_done": files_done,
                    "files_total": total,
                    "entities": engine._entity_graph.count_entities(),
                    "triples": engine._entity_graph.count_triples(),
                    "skipped": True,
                })
                continue
            await engine.index_source(entry.path, content)
            mark_indexed(entry.path, content)
            files_indexed += 1
            files_done += 1
            entities_now = engine._entity_graph.count_entities()
            triples_now = engine._entity_graph.count_triples()
            yield _sse("file", {
                "path": entry.path,
                "files_done": files_done,
                "files_total": total,
                "entities": entities_now,
                "triples": triples_now,
                "skipped": False,
            })
        except Exception as exc:
            yield _sse("error", {"path": entry.path, "detail": str(exc)})
            files_done += 1

    elapsed = round(time.monotonic() - t0, 1)
    entities_after = engine._entity_graph.count_entities()
    triples_after = engine._entity_graph.count_triples()
    yield _sse("stats", {
        "files_done": files_done,
        "files_total": total,
        "files_indexed": files_indexed,
        "files_skipped": files_skipped,
        "entities": entities_after,
        "triples": triples_after,
        "entities_added": entities_after - entities_before,
        "triples_added": triples_after - triples_before,
        "elapsed_s": elapsed,
    })
    yield _sse("done", {})
    log.info(
        "[graphrag] %s complete (%d indexed, %d skipped, %.1fs)",
        label, files_indexed, files_skipped, elapsed,
    )


def entities_for_source(engine: Any, source_path: str) -> list[dict[str, Any]]:
    """Return [{id, name, type}] for all entities extracted from a vault file."""
    if engine is None:
        return []
    try:
        graph = engine._entity_graph
        rows = engine._chunk_db.execute(
            "SELECT id FROM chunks WHERE source_path = ?", (source_path,),
        ).fetchall()
        chunk_ids = [r[0] for r in rows]
        seen: set[int] = set()
        entities: list[dict[str, Any]] = []
        for cid in chunk_ids:
            for e in graph.entities_for_chunk(cid):
                if e.id not in seen:
                    seen.add(e.id)
                    entities.append({"id": e.id, "name": e.name, "type": e.type or ""})
        return entities
    except Exception:
        log.warning("[graphrag] entities_for_source failed for %s", source_path, exc_info=True)
        return []


def sources_for_entity(engine: Any, entity_id: int) -> list[str]:
    """Return distinct source paths for all chunks mentioning this entity."""
    if engine is None:
        return []
    try:
        graph = engine._entity_graph
        chunk_ids = graph.chunks_for_entity(entity_id)
        paths: list[str] = []
        seen: set[str] = set()
        for cid in chunk_ids:
            cd = engine._get_chunk(cid)
            if cd:
                sp = cd.get("source_path", "")
                if sp and sp not in seen:
                    seen.add(sp)
                    paths.append(sp)
        return paths
    except Exception:
        log.warning("[graphrag] sources_for_entity failed for %d", entity_id, exc_info=True)
        return []


def source_subgraph(engine: Any, source_paths: list[str]) -> dict[str, Any]:
    """Return entity nodes and edges for all entities from given source paths.

    Returns {"nodes": [{id, name, type, degree}], "edges": [{source, target, relation, strength}]}.
    Only includes triples where BOTH endpoints are in the entity set (keeps graph cohesive).
    """
    if engine is None:
        return {"nodes": [], "edges": []}
    try:
        graph = engine._entity_graph
        entity_ids: set[int] = set()
        for sp in source_paths:
            rows = engine._chunk_db.execute(
                "SELECT id FROM chunks WHERE source_path = ?", (sp,),
            ).fetchall()
            for (cid,) in rows:
                for e in graph.entities_for_chunk(cid):
                    entity_ids.add(e.id)

        if not entity_ids:
            return {"nodes": [], "edges": []}

        nodes: list[dict[str, Any]] = []
        for eid in sorted(entity_ids):
            e = graph.get_entity(eid)
            if e is None:
                continue
            nodes.append({
                "id": e.id,
                "name": e.name,
                "type": e.type or "",
                "degree": graph.entity_degree(e.id),
            })

        edges: list[dict[str, Any]] = []
        seen_edges: set[tuple[int, int, str]] = set()
        for eid in entity_ids:
            for t in graph.get_entity_triples(eid):
                other_id = t.tail_id if t.head_id == eid else t.head_id
                if other_id not in entity_ids:
                    continue
                key = (min(t.head_id, t.tail_id), max(t.head_id, t.tail_id), t.relation)
                if key not in seen_edges:
                    seen_edges.add(key)
                    edges.append({
                        "source": t.head_id,
                        "target": t.tail_id,
                        "relation": t.relation,
                        "strength": t.strength,
                    })

        return {"nodes": nodes, "edges": edges}
    except Exception:
        log.warning("[graphrag] source_subgraph failed", exc_info=True)
        return {"nodes": [], "edges": []}
