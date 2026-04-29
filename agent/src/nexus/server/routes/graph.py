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
    # Dedupe by (other_entity, relation, direction) — triples are stored
    # one row per chunk-mention, so the same logical relation appears
    # multiple times. Show each fact once, with the highest strength.
    rel_map: dict[tuple[int, str, str], dict] = {}
    for t in triples:
        other_id = t.tail_id if t.head_id == entity_id else t.head_id
        direction = "outgoing" if t.head_id == entity_id else "incoming"
        key = (other_id, t.relation, direction)
        existing = rel_map.get(key)
        if existing is not None and t.strength <= existing["strength"]:
            continue
        other = graph.get_entity(other_id)
        rel_map[key] = {
            "entity_id": other_id,
            "entity_name": other.name if other else "?",
            "entity_type": other.type if other else "?",
            "relation": t.relation,
            "direction": direction,
            "strength": t.strength,
        }
    relations = list(rel_map.values())
    return {
        "enabled": True,
        "entity": {"id": entity.id, "name": entity.name, "type": entity.type, "description": entity.description},
        "degree": graph.entity_degree(entity_id),
        "relations": relations,
        "chunks": chunks,
    }


@router.get("/graph/knowledge/subgraph")
async def knowledge_subgraph(seed: int, hops: int = 2, width: int = 50) -> dict:
    """Return the seed's neighbourhood. ``width`` caps how many triples a
    single hub contributes per visit — strongest first — so exploring a
    high-degree node doesn't blow up response size."""
    from ...agent.graphrag_manager import get_engine
    engine = get_engine()
    if engine is None:
        return {"nodes": [], "edges": [], "enabled": False}
    result = engine._entity_graph.subgraph(seed, max_hops=hops, max_neighbors_per_node=width)
    # Dedupe (head, relation, tail) — the underlying triples table
    # stores one row per chunk-mention for provenance, so the same
    # logical edge can appear N times. Without dedup, the right panel
    # shows duplicate "uses → AutoGen" entries and the visual link
    # weight is misleading.
    edge_map: dict[tuple[int, str, int], dict] = {}
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
        "enabled": True,
        "nodes": [
            {"id": n.id, "name": n.name, "type": n.type, "degree": engine._entity_graph.entity_degree(n.id)}
            for n in result["nodes"]
        ],
        "edges": list(edge_map.values()),
    }


@router.get("/graph/knowledge/health")
async def knowledge_health() -> dict:
    """Report whether GraphRAG initialized successfully.

    ``ready=false`` with ``error`` set means a configured model could not
    be resolved; the UI should surface this and block reindex attempts.
    """
    from ...agent.graphrag_manager import get_health
    return get_health()


@router.get("/graph/knowledge/recent-errors")
async def knowledge_recent_errors() -> dict:
    """Up to 50 most-recent per-file indexing failures (newest last)."""
    from ...agent.graphrag_manager import get_recent_errors
    return {"errors": get_recent_errors()}


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


# ---------------------------------------------------------------------------
# Per-folder ontology-isolated knowledge graphs
#
# Distinct from the global GraphRAG above: each tab here is scoped to a
# specific vault folder, has its own ontology snapshot, and its index lives
# inside the folder itself (`<folder>/.nexus-graph/`). See
# `agent/src/nexus/agent/folder_graph/` for the implementation.
# ---------------------------------------------------------------------------

# In-flight indexing tasks per folder, mirrors `_graphrag_index_tasks` shape.
_folder_index_tasks: dict[str, dict[str, Any]] = {}


def _resolve_vault_folder(path: str) -> str:
    """Map a vault-relative folder path → absolute path on disk.

    The UI passes vault-relative paths (e.g. ``Projects/Alpha``); this resolver
    rejects absolute paths and any traversal outside the vault root.
    """
    from pathlib import Path
    from ...agent.graphrag_manager import get_home

    if not path or not path.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="`path` is required")
    rel = path.strip().strip("/")
    if rel.startswith("/") or ".." in rel.split("/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="path must be vault-relative")
    vault_root = (get_home() / "vault").resolve()
    abs_p = (vault_root / rel).resolve()
    try:
        abs_p.relative_to(vault_root)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="path escapes vault root")
    if not abs_p.is_dir():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="folder not found")
    return str(abs_p)


@router.post("/graph/folder/open")
async def folder_open(body: dict) -> dict:
    """Open or describe a folder graph. Does NOT trigger indexing."""
    from ...agent import folder_graph as fg

    abs_path = _resolve_vault_folder(body.get("path", ""))
    meta = fg.load_meta(abs_path)
    return {
        "path": body.get("path", "").strip().strip("/"),
        "abs_path": abs_path,
        "exists": bool(meta),
        "ontology": meta.get("ontology") if meta else None,
        "ontology_hash": meta.get("ontology_hash") if meta else None,
        "embedder_id": meta.get("embedder_id") if meta else None,
        "extractor_id": meta.get("extractor_id") if meta else None,
        "file_count": meta.get("file_count") if meta else 0,
        "last_indexed_at": meta.get("last_indexed_at") if meta else None,
    }


@router.get("/graph/folder/stale")
async def folder_stale(path: str) -> dict:
    from ...agent import folder_graph as fg

    abs_path = _resolve_vault_folder(path)
    return fg.stale_files(abs_path)


@router.get("/graph/folder/ontology")
async def folder_get_ontology(path: str) -> dict:
    from ...agent import folder_graph as fg

    abs_path = _resolve_vault_folder(path)
    meta = fg.load_meta(abs_path)
    return {
        "ontology": meta.get("ontology") if meta else None,
        "ontology_hash": meta.get("ontology_hash") if meta else None,
        "exists": bool(meta),
    }


@router.put("/graph/folder/ontology")
async def folder_put_ontology(body: dict) -> dict:
    """Persist a new ontology snapshot. Does NOT trigger reindex —
    the UI prompts the user separately."""
    from ...agent import folder_graph as fg

    abs_path = _resolve_vault_folder(body.get("path", ""))
    ontology = body.get("ontology") or {}
    if not isinstance(ontology, dict):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="`ontology` must be an object")
    fg.save_meta(abs_path, ontology=ontology)
    meta = fg.load_meta(abs_path)
    return {"saved": True, "ontology_hash": meta.get("ontology_hash")}


@router.post("/graph/folder/index")
async def folder_index(
    body: dict,
    app_state: dict[str, Any] = Depends(get_app_state),
) -> StreamingResponse:
    """Index a folder. SSE stream emits phase / file / stats / done events."""
    from ...agent import folder_graph as fg

    abs_path = _resolve_vault_folder(body.get("path", ""))
    full = bool(body.get("full", False))
    cfg = app_state.get("cfg")

    meta = fg.load_meta(abs_path)
    ontology = meta.get("ontology") or body.get("ontology")
    if not ontology:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="folder has no ontology — call PUT /graph/folder/ontology first",
        )

    async def _gen():
        # Track this task so cancel can hit it. Re-entrant calls share the slot.
        _folder_index_tasks[abs_path] = {"status": "indexing"}
        try:
            async for frame in fg.index_folder_streaming(
                abs_path, cfg=cfg, ontology=ontology, full=full
            ):
                yield frame
        finally:
            _folder_index_tasks.pop(abs_path, None)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/graph/folder/index-cancel")
async def folder_index_cancel(body: dict) -> dict:
    abs_path = _resolve_vault_folder(body.get("path", ""))
    info = _folder_index_tasks.get(abs_path)
    if not info:
        return {"cancelled": False, "reason": "no active job"}
    info["cancelled"] = True
    return {"cancelled": True, "path": body.get("path", "")}


@router.get("/graph/folder/full-subgraph")
async def folder_full_subgraph(
    path: str,
    max_nodes: int = 500,
    app_state: dict[str, Any] = Depends(get_app_state),
) -> dict:
    from ...agent import folder_graph as fg

    abs_path = _resolve_vault_folder(path)
    meta = fg.load_meta(abs_path)
    if not meta or not meta.get("ontology"):
        return {"nodes": [], "edges": [], "exists": False}
    cfg = app_state.get("cfg")
    try:
        entry = fg.open_folder_engine(abs_path, meta["ontology"], cfg)
    except Exception as exc:
        log.exception("[folder_graph] open failed for %s", abs_path)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=str(exc))
    sg = fg.full_subgraph(entry["engine"], max_nodes=max_nodes)
    sg["exists"] = True
    return sg


@router.get("/graph/folder/subgraph")
async def folder_subgraph(
    path: str,
    seed: int,
    hops: int = 2,
    width: int = 50,
    app_state: dict[str, Any] = Depends(get_app_state),
) -> dict:
    from ...agent import folder_graph as fg

    abs_path = _resolve_vault_folder(path)
    meta = fg.load_meta(abs_path)
    if not meta or not meta.get("ontology"):
        return {"nodes": [], "edges": []}
    cfg = app_state.get("cfg")
    entry = fg.open_folder_engine(abs_path, meta["ontology"], cfg)
    return fg.subgraph_for_seed(entry["engine"], seed, hops=hops, width=width)


@router.post("/graph/folder/query")
async def folder_query(
    body: dict,
    app_state: dict[str, Any] = Depends(get_app_state),
) -> dict:
    from ...agent import folder_graph as fg

    abs_path = _resolve_vault_folder(body.get("path", ""))
    q = (body.get("query") or "").strip()
    limit = min(int(body.get("limit", 10)), 50)
    if not q:
        return {"results": [], "subgraph": {"nodes": [], "edges": []}}
    meta = fg.load_meta(abs_path)
    if not meta or not meta.get("ontology"):
        return {"results": [], "subgraph": {"nodes": [], "edges": []}}
    cfg = app_state.get("cfg")
    entry = fg.open_folder_engine(abs_path, meta["ontology"], cfg)
    return await fg.query(entry["engine"], q, limit=limit)


@router.post("/graph/folder/ontology-wizard/start")
async def folder_wizard_start(
    body: dict,
    app_state: dict[str, Any] = Depends(get_app_state),
) -> StreamingResponse:
    from ...agent import folder_graph as fg

    abs_path = _resolve_vault_folder(body.get("path", ""))
    cfg = app_state.get("cfg")

    async def _gen():
        async for frame in fg.start_wizard(abs_path, cfg):
            yield frame

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/graph/folder/ontology-wizard/answer")
async def folder_wizard_answer(body: dict) -> dict:
    from ...agent import folder_graph as fg

    wizard_id = (body.get("wizard_id") or "").strip()
    answer = body.get("answer") or ""
    if not wizard_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="`wizard_id` required")
    accepted = fg.answer_wizard(wizard_id, str(answer))
    if not accepted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="wizard session not found or already finished")
    return {"accepted": True}


@router.get("/graph/folder/tabs")
async def folder_tabs_get() -> dict:
    from ...agent import folder_graph as fg
    return {"tabs": fg.list_tabs()}


@router.put("/graph/folder/tabs")
async def folder_tabs_put(body: dict) -> dict:
    from ...agent import folder_graph as fg
    tabs = body.get("tabs") or []
    if not isinstance(tabs, list):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="`tabs` must be a list")
    return {"tabs": fg.set_tabs(tabs)}


@router.delete("/graph/folder")
async def folder_delete(path: str) -> dict:
    from ...agent import folder_graph as fg
    abs_path = _resolve_vault_folder(path)
    removed = fg.delete_folder_index(abs_path)
    fg.remove_tab(abs_path)
    return {"removed": removed}


@router.post("/graphrag/reindex")
async def graphrag_reindex(
    full: bool = False,
    app_state: dict[str, Any] = Depends(get_app_state),
) -> StreamingResponse:
    """Reindex vault into GraphRAG, streaming progress as SSE.

    Query param ``full=1`` drops all existing data first.
    Default (incremental) skips files whose content hasn't changed.
    """
    from ...agent.graphrag_manager import get_engine, get_health, index_vault_streaming
    health = get_health()
    if not health.get("ready"):
        # Differentiate "user disabled it" from "init blew up" so the UI can
        # show an actionable message. Without this, both surface as the same
        # generic "GraphRAG not ready" string.
        if health.get("error"):
            detail = health["error"]
        elif not health.get("enabled"):
            detail = (
                "GraphRAG is disabled. Set `graphrag.enabled = true` in "
                "~/.nexus/config.toml and restart Nexus."
            )
        else:
            detail = "GraphRAG not ready"
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
        )
    if get_engine() is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GraphRAG engine not initialized",
        )
    nexus_cfg = app_state.get("cfg")

    async def _gen():
        async for frame in index_vault_streaming(nexus_cfg, full=full):
            yield frame

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
