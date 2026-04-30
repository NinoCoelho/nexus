"""LRU pool of per-folder GraphRAG engines + manifest connections.

The embedder is held in a module-level singleton, so evicting an idle
engine does not unload the (slow-to-load) embedding model — subsequent
folders can reuse it instantly. Engines themselves are lightweight on
top of that: SQLite handles + a reference to the shared embedder.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from collections import OrderedDict
from pathlib import Path
from typing import Any

from ._storage import (
    folder_dot_dir,
    is_initialized,
    normalize_folder,
    ontology_hash,
    open_manifest,
)

log = logging.getLogger(__name__)

POOL_SIZE = 6

# {abs_folder_str: {"engine": GraphRAGEngine, "manifest": sqlite3.Connection,
#                   "embedder_id": str, "extractor_id": str}}
_pool: "OrderedDict[str, dict[str, Any]]" = OrderedDict()

# Module-level embedder cache: same embedder is reused across all open folders
# so model load only happens once per process.
_embedder_cache: dict[str, Any] = {}  # {embedder_id: provider}


def _effective_embedder_id(cfg: Any, graphrag_cfg: Any) -> str:
    """Mirror of ``graphrag_manager._effective_embedder_id`` (private there)."""
    pinned = getattr(graphrag_cfg, "embedding_model_id", "") or ""
    if pinned:
        return pinned
    emb_cfg = graphrag_cfg.embeddings
    model = (getattr(emb_cfg, "model", "") or "").strip()
    if model:
        return model
    from ..builtin_embedder import BUILTIN_MODEL
    return BUILTIN_MODEL


def _get_or_load_embedder(cfg: Any, graphrag_cfg: Any, embedder_id: str) -> Any:
    cached = _embedder_cache.get(embedder_id)
    if cached is not None:
        return cached
    from ..graphrag_manager._resolvers import resolve_embedder
    emb = resolve_embedder(cfg, graphrag_cfg)
    _embedder_cache[embedder_id] = emb
    return emb


def _evict_one() -> None:
    if not _pool:
        return
    key, entry = _pool.popitem(last=False)
    try:
        entry["engine"].close()
    except Exception:
        log.warning("[folder_graph] engine close failed for %s", key, exc_info=True)
    try:
        entry["manifest"].close()
    except Exception:
        log.warning("[folder_graph] manifest close failed for %s", key, exc_info=True)


def _build_engine(folder: Path, ontology: dict[str, Any], cfg: Any,
                  graphrag_cfg: Any) -> tuple[Any, str, str]:
    """Construct a fresh loom GraphRAGEngine pointed at the folder's hidden dir.

    Returns ``(engine, embedder_id, extractor_id)``.
    """
    from loom.store.graphrag import (
        EmbeddingConfig,
        ExtractionConfig,
        GraphRAGConfig,
        GraphRAGEngine,
        OntologyConfig,
    )
    from ..graphrag_manager._resolvers import resolve_extraction_llm

    # Compute the effective embedder id the same way graphrag_manager does so
    # the cache key matches the global pipeline's identity.
    embedder_id = _effective_embedder_id(cfg, graphrag_cfg)

    embedder = _get_or_load_embedder(cfg, graphrag_cfg, embedder_id)

    emb_cfg = graphrag_cfg.embeddings
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
        ontology=OntologyConfig(
            entity_types=list(ontology.get("entity_types") or []),
            core_relations=list(ontology.get("relations") or []),
            allow_custom_relations=bool(ontology.get("allow_custom_relations", True)),
        ),
        max_hops=graphrag_cfg.max_hops,
        context_budget=graphrag_cfg.context_budget,
        top_k=graphrag_cfg.top_k,
        chunk_size=graphrag_cfg.chunk_size,
    )

    llm = resolve_extraction_llm(cfg, graphrag_cfg)
    extractor_id = (
        getattr(graphrag_cfg, "extraction_model_id", "")
        or (getattr(graphrag_cfg.extraction, "model", "") or "")
        or "builtin"
    )

    db_dir = folder_dot_dir(folder)
    engine = GraphRAGEngine(engine_cfg, embedder, db_dir=db_dir, llm_provider=llm)
    return engine, embedder_id, extractor_id


def open_folder_engine(folder: str | Path, ontology: dict[str, Any],
                       cfg: Any) -> dict[str, Any]:
    """Open or return a cached engine + manifest for a folder.

    Always returns ``{"engine", "manifest", "embedder_id", "extractor_id"}``.
    Marks the entry most-recently-used in the LRU. Caller is responsible for
    *not* closing the manifest connection — eviction handles that.
    """
    graphrag_cfg = getattr(cfg, "graphrag", None)
    if graphrag_cfg is None or not getattr(graphrag_cfg, "enabled", True):
        raise RuntimeError(
            "GraphRAG is disabled in config — set graphrag.enabled = true"
        )

    folder_p = normalize_folder(folder)
    key = str(folder_p)
    desired_hash = ontology_hash(ontology)

    entry = _pool.pop(key, None)
    if entry is not None:
        if entry.get("ontology_hash") == desired_hash:
            _pool[key] = entry  # move to MRU
            return entry
        # Ontology drifted since this engine was built — its loom OntologyConfig
        # is stale, so any extraction would still use the old relation taxonomy.
        # Tear it down and fall through to a rebuild below.
        log.info("[folder_graph] ontology drift on %s — rebuilding engine", key)
        try:
            entry["engine"].close()
        except Exception:
            log.warning("[folder_graph] engine close failed during ontology rebuild for %s",
                        key, exc_info=True)
        try:
            entry["manifest"].close()
        except Exception:
            log.warning("[folder_graph] manifest close failed during ontology rebuild for %s",
                        key, exc_info=True)

    if len(_pool) >= POOL_SIZE:
        _evict_one()

    manifest = open_manifest(folder_p)
    engine, embedder_id, extractor_id = _build_engine(folder_p, ontology, cfg, graphrag_cfg)
    entry = {
        "engine": engine,
        "manifest": manifest,
        "embedder_id": embedder_id,
        "extractor_id": extractor_id,
        "ontology_hash": desired_hash,
    }
    _pool[key] = entry
    return entry


def close_folder_engine(folder: str | Path) -> None:
    folder_p = normalize_folder(folder)
    entry = _pool.pop(str(folder_p), None)
    if entry is None:
        return
    try:
        entry["engine"].close()
    except Exception:
        log.warning("[folder_graph] engine close failed", exc_info=True)
    try:
        entry["manifest"].close()
    except Exception:
        log.warning("[folder_graph] manifest close failed", exc_info=True)


def close_all_engines() -> None:
    while _pool:
        _evict_one()


def delete_folder_index(folder: str | Path) -> bool:
    """Close the engine and rm the hidden ``.nexus-graph`` directory.

    Returns True if a directory was removed. Idempotent.
    """
    folder_p = normalize_folder(folder)
    close_folder_engine(folder_p)
    dot = folder_dot_dir(folder_p)
    if not dot.is_dir():
        return False
    shutil.rmtree(dot, ignore_errors=True)
    return True


def get_pool_keys() -> list[str]:
    """For tests / diagnostics."""
    return list(_pool.keys())


def has_folder(folder: str | Path) -> bool:
    return is_initialized(folder)
