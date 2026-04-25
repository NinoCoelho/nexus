"""Routes for graph views: /graph, /graph/knowledge/*, /graphrag/reindex."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from ..deps import get_sessions, get_registry, get_app_state

log = logging.getLogger(__name__)

router = APIRouter()

# Tracks in-progress file indexing tasks: path → {status, ...}
_graphrag_index_tasks: dict[str, dict[str, Any]] = {}


@router.get("/graph")
async def get_agent_graph(
    sessions=Depends(get_sessions),
    registry=Depends(get_registry),
) -> dict:
    """Return the agent/skill/session graph for the UI graph view.
    Result is cached for 60 seconds to avoid rebuilding on every navigation."""
    from ..graph import build_agent_graph
    return build_agent_graph(registry, sessions)


@router.get("/graph/knowledge")
async def get_knowledge_graph() -> dict:
    """Return the GraphRAG entity/relation graph for the Knowledge tab."""
    from ...agent.graphrag_manager import get_engine
    engine = get_engine()
    if engine is None:
        return {"nodes": [], "edges": [], "enabled": False}
    return engine.export_graph()


@router.post("/graph/knowledge/query")
async def knowledge_query(body: dict) -> dict:
    """Semantic search over the knowledge graph. Returns evidence + trace + subgraph."""
    query = body.get("query", "").strip()
    limit = min(int(body.get("limit", 10)), 50)
    if not query:
        return {"results": [], "trace": None, "subgraph": {"nodes": [], "edges": []}}
    from ...agent.graphrag_manager import get_engine
    engine = get_engine()
    if engine is None:
        return {"results": [], "trace": None, "subgraph": {"nodes": [], "edges": []}, "enabled": False}
    enriched = await engine.retrieve_enriched(query, top_k=limit)
    return {
        "enabled": True,
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
        "trace": {
            "seed_entities": enriched.trace.seed_entities,
            "hops": [
                {"from": h.from_entity, "to": h.to_entity, "relation": h.relation, "depth": h.hop_depth}
                for h in enriched.trace.hops
            ],
            "expanded_entity_ids": enriched.trace.expanded_entity_ids,
        },
        "subgraph": {
            "nodes": enriched.subgraph_nodes,
            "edges": enriched.subgraph_edges,
        },
    }


@router.get("/graph/knowledge/entities")
async def knowledge_entities(
    type: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    from ...agent.graphrag_manager import get_engine
    engine = get_engine()
    if engine is None:
        return {"entities": [], "total": 0, "enabled": False}
    graph = engine._entity_graph
    entities = graph.list_entities(entity_type=type, search=search, limit=limit, offset=offset)
    total = graph.count_entities()
    return {
        "enabled": True,
        "entities": [
            {
                "id": e.id, "name": e.name, "type": e.type,
                "degree": graph.entity_degree(e.id),
            }
            for e in entities
        ],
        "total": total,
    }


@router.get("/graph/knowledge/entity/{entity_id}")
async def knowledge_entity_detail(entity_id: int) -> dict:
    from ...agent.graphrag_manager import get_engine
    engine = get_engine()
    if engine is None:
        return {"entity": None, "enabled": False}
    graph = engine._entity_graph
    entity = graph.get_entity(entity_id)
    if entity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="entity not found")
    triples = graph.get_entity_triples(entity_id)
    chunk_ids = graph.chunks_for_entity(entity_id)
    chunks = []
    for cid in chunk_ids[:20]:
        cd = engine._get_chunk(cid)
        if cd:
            chunks.append({"chunk_id": cid, "source_path": cd.get("source_path", ""), "heading": cd.get("heading", "")})
    relations = []
    for t in triples:
        other_id = t.tail_id if t.head_id == entity_id else t.head_id
        other = graph.get_entity(other_id)
        direction = "outgoing" if t.head_id == entity_id else "incoming"
        relations.append({
            "entity_id": other_id,
            "entity_name": other.name if other else "?",
            "entity_type": other.type if other else "?",
            "relation": t.relation,
            "direction": direction,
            "strength": t.strength,
        })
    return {
        "enabled": True,
        "entity": {"id": entity.id, "name": entity.name, "type": entity.type, "description": entity.description},
        "degree": graph.entity_degree(entity_id),
        "relations": relations,
        "chunks": chunks,
    }


@router.get("/graph/knowledge/subgraph")
async def knowledge_subgraph(seed: int, hops: int = 2) -> dict:
    from ...agent.graphrag_manager import get_engine
    engine = get_engine()
    if engine is None:
        return {"nodes": [], "edges": [], "enabled": False}
    result = engine._entity_graph.subgraph(seed, max_hops=hops)
    return {
        "enabled": True,
        "nodes": [
            {"id": n.id, "name": n.name, "type": n.type, "degree": engine._entity_graph.entity_degree(n.id)}
            for n in result["nodes"]
        ],
        "edges": [
            {"source": e.head_id, "target": e.tail_id, "relation": e.relation, "strength": e.strength}
            for e in result["edges"]
        ],
    }


@router.get("/graph/knowledge/stats")
async def knowledge_stats() -> dict:
    from ...agent.graphrag_manager import get_engine
    engine = get_engine()
    if engine is None:
        return {"enabled": False, "entities": 0, "triples": 0, "types": {}, "components": []}
    graph = engine._entity_graph
    components = graph.connected_components()
    return {
        "enabled": True,
        "entities": graph.count_entities(),
        "triples": graph.count_triples(),
        "types": graph.entity_counts_by_type(),
        "components": [
            {"id": i, "size": len(c), "entities": c[:10]}
            for i, c in enumerate(components[:20])
        ],
        "component_count": len(components),
    }


@router.get("/graph/knowledge/file-subgraph")
async def knowledge_file_subgraph(path: str) -> dict:
    """Return entity subgraph for all entities extracted from a vault file."""
    from ...agent.graphrag_manager import source_subgraph
    return source_subgraph([path])


@router.get("/graph/knowledge/folder-subgraph")
async def knowledge_folder_subgraph(folder: str) -> dict:
    """Return entity subgraph for all entities from vault files in a folder."""
    from ...agent.graphrag_manager import source_subgraph
    from ...vault import list_tree
    prefix = folder if folder.endswith("/") else folder + "/"
    entries = list_tree()
    paths = [e.path for e in entries if e.type == "file" and e.path.startswith(prefix)]
    if not paths:
        return {"nodes": [], "edges": []}
    return source_subgraph(paths)


@router.post("/graph/knowledge/index-file")
async def graphrag_index_file(body: dict) -> dict:
    from ...agent.graphrag_manager import get_engine, index_vault_file, source_subgraph
    from ...vault import read_file

    path = body.get("path", "").strip()
    if not path:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`path` required")
    if get_engine() is None:
        return {"enabled": False}
    try:
        result = read_file(path)
        content = result.get("content", "")
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file not found")
    if not content.strip():
        return {"queued": False, "reason": "empty file"}

    existing = _graphrag_index_tasks.get(path)
    if existing and existing.get("status") == "indexing":
        return {"queued": True, "path": path, "already_running": True}

    _graphrag_index_tasks[path] = {"status": "indexing", "task": None}

    async def _run() -> None:
        try:
            await index_vault_file(path, content)
            sg = source_subgraph([path])
            _graphrag_index_tasks[path] = {
                "status": "done",
                "node_count": len(sg.get("nodes", [])),
                "edge_count": len(sg.get("edges", [])),
            }
        except asyncio.CancelledError:
            _graphrag_index_tasks[path] = {"status": "cancelled"}
            raise
        except Exception as exc:
            log.exception("graphrag index-file failed for %s", path)
            detail = str(exc) or exc.__class__.__name__
            if "ConnectError" in exc.__class__.__name__ or "All connection attempts failed" in detail:
                detail = "Cannot reach embedding/extraction model — check that the configured provider (e.g. Ollama) is running"
            _graphrag_index_tasks[path] = {"status": "error", "detail": detail}

    task = asyncio.get_running_loop().create_task(_run())
    _graphrag_index_tasks[path]["task"] = task
    return {"queued": True, "path": path}


def _index_progress(path: str) -> dict:
    """Count chunks/entities for a source by querying GraphRAG SQLite directly."""
    import sqlite3
    from ...agent.graphrag_manager import get_home
    db_dir = get_home() / "graphrag"
    chunks_db = db_dir / "graphrag_chunks.sqlite"
    ents_db = db_dir / "graphrag_entities.sqlite"
    if not chunks_db.exists() or not ents_db.exists():
        return {"total_chunks": 0, "processed_chunks": 0}
    try:
        conn = sqlite3.connect(f"file:{chunks_db}?mode=ro", uri=True)
        try:
            conn.execute(f"ATTACH DATABASE 'file:{ents_db}?mode=ro' AS ents KEY ''")
        except sqlite3.OperationalError:
            conn.execute(f"ATTACH DATABASE '{ents_db}' AS ents")
        cur = conn.execute("SELECT count(*) FROM chunks WHERE source_path = ?", (path,))
        total = cur.fetchone()[0] or 0
        cur = conn.execute(
            "SELECT count(DISTINCT em.chunk_id) FROM ents.entity_mentions em "
            "JOIN chunks ch ON ch.id = em.chunk_id WHERE ch.source_path = ?",
            (path,),
        )
        processed = cur.fetchone()[0] or 0
        conn.close()
        return {"total_chunks": int(total), "processed_chunks": int(processed)}
    except Exception:
        log.warning("[graphrag] progress query failed", exc_info=True)
        return {"total_chunks": 0, "processed_chunks": 0}


@router.post("/graph/knowledge/index-file-cancel")
async def graphrag_index_file_cancel(body: dict) -> dict:
    path = (body.get("path") or "").strip()
    info = _graphrag_index_tasks.get(path)
    if not info or info.get("status") != "indexing":
        return {"cancelled": False, "reason": "no active job"}
    task = info.get("task")
    if task is not None and not task.done():
        task.cancel()
    return {"cancelled": True, "path": path}


@router.get("/graph/knowledge/index-file-status")
async def graphrag_index_file_status(path: str) -> dict:
    info = _graphrag_index_tasks.get(path)
    if info is None:
        return {"status": "unknown"}
    result = {k: v for k, v in info.items() if k != "task"}
    if info.get("status") == "indexing":
        result.update(_index_progress(path))
    elif info.get("status") == "done":
        from ...agent.graphrag_manager import source_subgraph
        sg = source_subgraph([path])
        result["nodes"] = sg.get("nodes", [])
        result["edges"] = sg.get("edges", [])
        _graphrag_index_tasks.pop(path, None)
    elif info.get("status") in ("error", "cancelled"):
        _graphrag_index_tasks.pop(path, None)
    return result


@router.post("/graphrag/reindex")
async def graphrag_reindex(
    full: bool = False,
    app_state: dict[str, Any] = Depends(get_app_state),
) -> StreamingResponse:
    """Reindex vault into GraphRAG, streaming progress as SSE.

    Query param ``full=1`` drops all existing data first.
    Default (incremental) skips files whose content hasn't changed.
    """
    from ...agent.graphrag_manager import index_vault_streaming
    nexus_cfg = app_state.get("cfg")

    async def _gen():
        async for frame in index_vault_streaming(nexus_cfg, full=full):
            yield frame

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
