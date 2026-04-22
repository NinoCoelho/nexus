"""GraphRAG engine lifecycle manager for Nexus.

Initializes the :class:`~loom.store.graphrag.GraphRAGEngine` from config,
manages the singleton instance, and provides vault indexing hooks.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger(__name__)

_engine: Any | None = None
_home: Path | None = None
_manifest_db: sqlite3.Connection | None = None


def get_home() -> Path:
    if _home is not None:
        return _home
    return Path.home() / ".nexus"


def get_engine() -> Any | None:
    return _engine


def _get_manifest(db_dir: Path) -> sqlite3.Connection:
    global _manifest_db
    if _manifest_db is not None:
        return _manifest_db
    _manifest_db = sqlite3.connect(
        str(db_dir / "graphrag_manifest.sqlite"), check_same_thread=False,
    )
    _manifest_db.execute("PRAGMA journal_mode=WAL")
    _manifest_db.execute("""
        CREATE TABLE IF NOT EXISTS content_hashes (
            source_path TEXT PRIMARY KEY,
            content_hash TEXT NOT NULL,
            indexed_at REAL NOT NULL
        )
    """)
    _manifest_db.commit()
    return _manifest_db


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _is_indexed(path: str, content: str) -> bool:
    if _manifest_db is None:
        return False
    row = _manifest_db.execute(
        "SELECT content_hash FROM content_hashes WHERE source_path = ?", (path,),
    ).fetchone()
    return row is not None and row[0] == _content_hash(content)


def _mark_indexed(path: str, content: str) -> None:
    if _manifest_db is None:
        return
    _manifest_db.execute(
        "INSERT OR REPLACE INTO content_hashes (source_path, content_hash, indexed_at) "
        "VALUES (?, ?, ?)",
        (path, _content_hash(content), time.time()),
    )
    _manifest_db.commit()


def _clear_manifest() -> None:
    global _manifest_db
    if _manifest_db is not None:
        _manifest_db.execute("DELETE FROM content_hashes")
        _manifest_db.commit()


async def initialize(cfg: Any) -> None:
    """Create the GraphRAG engine from Nexus config if enabled."""
    global _engine, _home

    graphrag_cfg = getattr(cfg, "graphrag", None)
    if graphrag_cfg is None or not getattr(graphrag_cfg, "enabled", False):
        log.info("[graphrag] disabled in config")
        return

    from loom.store.embeddings import OllamaEmbeddingProvider, OpenAIEmbeddingProvider
    from loom.store.graphrag import GraphRAGConfig, GraphRAGEngine

    _home = get_home()
    db_dir = _home / "graphrag"
    db_dir.mkdir(parents=True, exist_ok=True)

    _get_manifest(db_dir)

    emb_cfg = graphrag_cfg.embeddings
    if emb_cfg.provider == "openai":
        embedder = OpenAIEmbeddingProvider(
            model=emb_cfg.model,
            base_url=emb_cfg.base_url,
            key_env=emb_cfg.key_env,
            dim=emb_cfg.dimensions,
        )
    else:
        embedder = OllamaEmbeddingProvider(
            model=emb_cfg.model,
            base_url=emb_cfg.base_url,
            dim=emb_cfg.dimensions,
        )

    from loom.store.graphrag import (
        EmbeddingConfig,
        ExtractionConfig,
        OntologyConfig,
    )

    ont = graphrag_cfg.ontology
    ontology_cfg = OntologyConfig(
        entity_types=ont.entity_types,
        core_relations=ont.core_relations,
        allow_custom_relations=ont.allow_custom_relations,
    )

    engine_cfg = GraphRAGConfig(
        enabled=True,
        embeddings=EmbeddingConfig(
            provider=emb_cfg.provider,
            model=emb_cfg.model,
            base_url=emb_cfg.base_url,
            key_env=emb_cfg.key_env,
            dimensions=emb_cfg.dimensions,
        ),
        extraction=ExtractionConfig(
            model=graphrag_cfg.extraction.model,
            max_gleanings=graphrag_cfg.extraction.max_gleanings,
        ),
        ontology=ontology_cfg,
        max_hops=graphrag_cfg.max_hops,
        context_budget=graphrag_cfg.context_budget,
        top_k=graphrag_cfg.top_k,
        chunk_size=graphrag_cfg.chunk_size,
    )

    llm_for_extraction = _resolve_extraction_llm(cfg, graphrag_cfg)

    _engine = GraphRAGEngine(
        engine_cfg,
        embedder,
        db_dir=db_dir,
        llm_provider=llm_for_extraction,
    )
    log.info(
        "[graphrag] initialized (embeddings=%s/%s, db=%s)",
        emb_cfg.provider, emb_cfg.model, db_dir,
    )


def _resolve_extraction_llm(cfg: Any, graphrag_cfg: Any) -> Any | None:
    extraction_model = graphrag_cfg.extraction.model
    if extraction_model is None:
        return None

    # First try: match against configured models/providers (e.g. "zai/glm-4-air")
    try:
        from .registry import build_registry
        registry = build_registry(cfg)
        provider, _ = registry.get_for_model(extraction_model)
        from ._loom_bridge import LoomProviderAdapter
        return LoomProviderAdapter(provider)
    except Exception:
        pass

    # Fallback: direct Ollama / OpenAI-compat provider
    ext_cfg = graphrag_cfg.extraction
    try:
        from .llm import OpenAIProvider
        base_url = ext_cfg.base_url
        api_key = "ollama"
        if ext_cfg.key_env:
            import os
            api_key = os.environ.get(ext_cfg.key_env, "")
        provider = OpenAIProvider(base_url=base_url, api_key=api_key, model=extraction_model)
        from ._loom_bridge import LoomProviderAdapter
        return LoomProviderAdapter(provider)
    except Exception:
        log.warning("[graphrag] failed to create extraction LLM provider", exc_info=True)
        return None


async def index_vault_file(path: str, content: str) -> None:
    if _engine is None:
        return
    if _is_indexed(path, content):
        return
    try:
        await _engine.index_source(path, content)
        _mark_indexed(path, content)
    except Exception:
        log.warning("[graphrag] failed to index %s", path, exc_info=True)


async def index_full_vault() -> None:
    if _engine is None:
        return
    try:
        from .. import vault
        entries = vault.list_tree()
        for entry in entries:
            if entry.type != "file":
                continue
            try:
                result = vault.read_file(entry.path)
                content = result.get("content", "")
                if content:
                    await _engine.index_source(entry.path, content)
            except Exception:
                log.warning("[graphrag] failed to index %s", entry.path, exc_info=True)
        log.info("[graphrag] full vault index complete (%d files)", len(entries))
    except Exception:
        log.error("[graphrag] full vault index failed", exc_info=True)


async def index_vault_streaming(cfg: Any, *, full: bool = False) -> Iterator[str]:
    """Yield SSE frames while indexing the vault into GraphRAG.

    When ``full=True``, drops all data first and reindexes every file.
    When ``full=False`` (default), skips files whose content hash hasn't
    changed since the last successful index (incremental).

    Each frame is ``event: <type>\\ndata: <json>\\n\\n``.
    Event types: ``status``, ``file``, ``error``, ``stats``, ``done``.
    """
    if full:
        yield _sse("status", {"message": "Dropping existing index…"})
        drop_data()
        yield _sse("status", {"message": "Reinitializing engine…"})
        await initialize(cfg)

    if _engine is None:
        yield _sse("error", {"detail": "GraphRAG engine not initialized"})
        return

    yield _sse("status", {"message": "Scanning vault files…"})

    from .. import vault
    entries = vault.list_tree()
    files = [e for e in entries if e.type == "file"]
    total = len(files)
    label = "full reindex" if full else "incremental update"
    yield _sse("status", {"message": f"Found {total} vault file(s) — {label}"})

    files_done = 0
    files_skipped = 0
    files_indexed = 0
    entities_before = _engine._entity_graph.count_entities()
    triples_before = _engine._entity_graph.count_triples()
    t0 = time.monotonic()

    for entry in files:
        try:
            result = vault.read_file(entry.path)
            content = result.get("content", "")
            if not content:
                files_done += 1
                continue
            if not full and _is_indexed(entry.path, content):
                files_skipped += 1
                files_done += 1
                yield _sse("file", {
                    "path": entry.path,
                    "files_done": files_done,
                    "files_total": total,
                    "entities": _engine._entity_graph.count_entities(),
                    "triples": _engine._entity_graph.count_triples(),
                    "skipped": True,
                })
                continue
            await _engine.index_source(entry.path, content)
            _mark_indexed(entry.path, content)
            files_indexed += 1
            files_done += 1
            entities_now = _engine._entity_graph.count_entities()
            triples_now = _engine._entity_graph.count_triples()
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
    entities_after = _engine._entity_graph.count_entities()
    triples_after = _engine._entity_graph.count_triples()
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


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def drop_data() -> int:
    """Delete all GraphRAG SQLite databases under ~/.nexus/graphrag/.

    Returns the number of database files removed.
    """
    global _engine, _manifest_db
    home = get_home()
    db_dir = home / "graphrag"
    if not db_dir.is_dir():
        return 0
    if _engine is not None:
        _engine.close()
        _engine = None
    if _manifest_db is not None:
        _manifest_db.close()
        _manifest_db = None
    count = 0
    for f in db_dir.glob("*.sqlite"):
        f.unlink(missing_ok=True)
        count += 1
    for f in db_dir.glob("*.sqlite-wal"):
        f.unlink(missing_ok=True)
    for f in db_dir.glob("*.sqlite-shm"):
        f.unlink(missing_ok=True)
    log.info("[graphrag] dropped %d database file(s) from %s", count, db_dir)
    return count


def build_graphrag_for_agent(cfg: Any) -> Any | None:
    if _engine is None:
        return None
    return _engine


def entities_for_source(source_path: str) -> list[dict[str, Any]]:
    """Return [{id, name, type}] for all entities extracted from a vault file."""
    if _engine is None:
        return []
    try:
        graph = _engine._entity_graph
        rows = _engine._chunk_db.execute(
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


def sources_for_entity(entity_id: int) -> list[str]:
    """Return distinct source paths for all chunks mentioning this entity."""
    if _engine is None:
        return []
    try:
        graph = _engine._entity_graph
        chunk_ids = graph.chunks_for_entity(entity_id)
        paths: list[str] = []
        seen: set[str] = set()
        for cid in chunk_ids:
            cd = _engine._get_chunk(cid)
            if cd:
                sp = cd.get("source_path", "")
                if sp and sp not in seen:
                    seen.add(sp)
                    paths.append(sp)
        return paths
    except Exception:
        log.warning("[graphrag] sources_for_entity failed for %d", entity_id, exc_info=True)
        return []


def source_subgraph(source_paths: list[str]) -> dict[str, Any]:
    """Return entity nodes and edges for all entities extracted from given source paths.

    Returns {"nodes": [{id, name, type, degree}], "edges": [{source, target, relation, strength}]}.
    Only includes triples where BOTH endpoints are in the entity set (keeps graph cohesive).
    """
    if _engine is None:
        return {"nodes": [], "edges": []}
    try:
        graph = _engine._entity_graph
        entity_ids: set[int] = set()
        for sp in source_paths:
            rows = _engine._chunk_db.execute(
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
