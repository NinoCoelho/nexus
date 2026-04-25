"""GraphRAG engine lifecycle manager for Nexus.

Initializes the :class:`~loom.store.graphrag.GraphRAGEngine` from config,
manages the singleton instance, and provides vault indexing hooks.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Iterator

from ._manifest import (
    clear_manifest as clear_manifest,
    close_manifest,
    is_indexed as _is_indexed,
    mark_indexed as _mark_indexed,
    open_manifest as _get_manifest,
    remove_path as _manifest_remove_path,
)
from ._resolvers import resolve_embedder as _resolve_embedder, resolve_extraction_llm as _resolve_extraction_llm

log = logging.getLogger(__name__)

_engine: Any | None = None
_home: Path | None = None


def get_home() -> Path:
    if _home is not None:
        return _home
    return Path.home() / ".nexus"


def get_engine() -> Any | None:
    return _engine


async def initialize(cfg: Any) -> None:
    """Create the GraphRAG engine from Nexus config if enabled."""
    global _engine, _home

    graphrag_cfg = getattr(cfg, "graphrag", None)
    if graphrag_cfg is None or not getattr(graphrag_cfg, "enabled", False):
        log.info("[graphrag] disabled in config")
        return

    if _engine is not None:
        try:
            _engine.close()
        except Exception:
            log.warning("[graphrag] failed to close previous engine", exc_info=True)
        _engine = None

    from loom.store.graphrag import GraphRAGConfig, GraphRAGEngine

    _home = get_home()
    db_dir = _home / "graphrag"
    db_dir.mkdir(parents=True, exist_ok=True)

    _get_manifest(db_dir)

    emb_cfg = graphrag_cfg.embeddings
    embedder = _resolve_embedder(cfg, graphrag_cfg)

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


async def index_vault_file(path: str, content: str) -> None:
    """Index a single vault file. Raises on failure so callers can surface errors.

    Fire-and-forget callers should use :func:`schedule_index` instead, which
    swallows exceptions to keep them out of the asyncio task error stream.
    """
    if _engine is None:
        return
    if _is_indexed(path, content):
        return
    await _engine.index_source(path, content)
    _mark_indexed(path, content)
    try:
        from ..server.event_bus import publish
        publish({"type": "graphrag.indexed", "path": path})
    except Exception:
        pass


async def _index_vault_file_silent(path: str, content: str) -> None:
    try:
        await index_vault_file(path, content)
    except Exception:
        log.warning("[graphrag] failed to index %s", path, exc_info=True)


def schedule_index(path: str, content: str) -> None:
    """Fire-and-forget GraphRAG indexing of a single vault file.

    Schedules ``index_vault_file`` on the running event loop so callers
    in synchronous code (``vault.write_file``) don't block on LLM/embedding
    calls. Silently skips if no loop is running or GraphRAG is disabled.
    """
    if _engine is None:
        return
    if not content:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_index_vault_file_silent(path, content))


def remove_source(path: str) -> None:
    """Remove a vault file's chunks/entities from GraphRAG and the manifest."""
    if _engine is None:
        return
    try:
        _engine.remove_source(path)
    except Exception:
        log.warning("[graphrag] remove_source failed for %s", path, exc_info=True)
    try:
        _manifest_remove_path(path)
    except Exception:
        log.warning("[graphrag] manifest remove_path failed for %s", path, exc_info=True)
    try:
        from ..server.event_bus import publish
        publish({"type": "graphrag.removed", "path": path})
    except Exception:
        pass


async def index_full_vault() -> None:
    if _engine is None:
        return
    try:
        from nexus import vault
        entries = vault.list_tree()
        for entry in entries:
            if entry.type != "file":
                continue
            try:
                result = vault.read_file(entry.path)
                content = result.get("content", "")
                if content:
                    await _index_vault_file_silent(entry.path, content)
            except Exception:
                log.warning("[graphrag] failed to read %s", entry.path, exc_info=True)
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
    from ._indexer import index_vault_streaming as _stream
    async for frame in _stream(_engine, cfg, full=full, initialize_fn=initialize, drop_data_fn=drop_data):
        yield frame


def drop_data() -> int:
    """Delete all GraphRAG SQLite databases under ~/.nexus/graphrag/.

    Returns the number of database files removed.
    """
    global _engine
    home = get_home()
    db_dir = home / "graphrag"
    if not db_dir.is_dir():
        return 0
    if _engine is not None:
        _engine.close()
        _engine = None
    close_manifest()
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
    from ._indexer import entities_for_source as _impl
    return _impl(_engine, source_path)


def sources_for_entity(entity_id: int) -> list[str]:
    """Return distinct source paths for all chunks mentioning this entity."""
    from ._indexer import sources_for_entity as _impl
    return _impl(_engine, entity_id)


def source_subgraph(source_paths: list[str]) -> dict[str, Any]:
    """Return entity nodes and edges for all entities from given source paths."""
    from ._indexer import source_subgraph as _impl
    return _impl(_engine, source_paths)
