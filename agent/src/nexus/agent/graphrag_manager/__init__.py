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
    get_meta as _get_index_meta,
    is_indexed as _is_indexed,
    mark_indexed as _mark_indexed,
    open_manifest as _get_manifest,
    remove_path as _manifest_remove_path,
    set_meta as _set_index_meta,
)
from collections import deque
from ._resolvers import (
    GraphRAGConfigError,
    resolve_embedder as _resolve_embedder,
    resolve_extraction_llm as _resolve_extraction_llm,
)

log = logging.getLogger(__name__)

_engine: Any | None = None
_home: Path | None = None
_last_init_error: str | None = None
# Tracks whether graphrag was enabled in config the last time initialize()
# ran. Lets get_health() report "disabled" distinctly from "init failed",
# so the UI can show an actionable message instead of a generic one.
_last_enabled: bool = False
# Independent of ``_last_init_error``: a non-fatal warning surfaced to the
# UI when the configured embedder differs from the one used to build the
# current vector store. The engine still initializes (so reads still work
# against the stale vectors), but new queries against new content drift in
# similarity geometry. User clears it by running ``nexus graphrag reindex``.
_stale_warning: str | None = None
# Bounded queue of recent per-file indexing failures, exposed via
# /graph/knowledge/recent-errors. Tuples of (path, error_message, ts).
_recent_errors: deque[dict[str, Any]] = deque(maxlen=50)
# Cap concurrent schedule_index() tasks so a vault-edit storm cannot pile up
# dozens of in-flight Ollama requests and saturate the httpx connection pool.
_INDEX_CONCURRENCY = 2
_index_semaphore: asyncio.Semaphore | None = None


def get_home() -> Path:
    if _home is not None:
        return _home
    return Path.home() / ".nexus"


def get_engine() -> Any | None:
    return _engine


def get_health() -> dict[str, Any]:
    """Return current GraphRAG readiness state for the UI.

    ``ready=True`` only when the engine initialized successfully. When false,
    ``error`` carries the human-readable cause from the last failed init —
    typically an unresolvable embedding model.
    """
    return {
        "ready": _engine is not None,
        "enabled": _last_enabled,
        "error": _last_init_error,
        "stale_warning": _stale_warning,
    }


def get_recent_errors() -> list[dict[str, Any]]:
    """Return up to 50 most-recent per-file index failures."""
    return list(_recent_errors)


def _effective_embedder_id(cfg: Any, graphrag_cfg: Any) -> str:
    """Return the embedder identifier actually used by ``_resolve_embedder``.

    The config has three slots that determine which embedder runs:
    ``embedding_model_id`` (registry-pinned), ``embeddings.model`` (provider-
    qualified name), and the builtin fallback. Mirrors the precedence in
    :func:`_resolvers.resolve_embedder` so the manifest tracks the same
    string the engine just loaded — otherwise stale detection misses the
    common case where users leave ``embeddings.model`` blank ("use builtin").
    """
    pinned = getattr(graphrag_cfg, "embedding_model_id", "") or ""
    if pinned:
        return pinned
    emb_cfg = graphrag_cfg.embeddings
    model = (getattr(emb_cfg, "model", "") or "").strip()
    if model:
        return model
    from nexus.agent.builtin_embedder import BUILTIN_MODEL
    return BUILTIN_MODEL


def _record_error(path: str, exc: BaseException) -> None:
    import time
    msg = str(exc) or exc.__class__.__name__
    _recent_errors.append({"path": path, "error": msg, "ts": time.time()})
    try:
        from ..server.event_bus import publish
        publish({"type": "graphrag.index_failed", "path": path, "error": msg})
    except Exception:
        pass


async def initialize(cfg: Any) -> None:
    """Create the GraphRAG engine from Nexus config if enabled.

    On failure to resolve a configured model, records the error in
    ``_last_init_error`` and leaves ``_engine`` as ``None``. The exception
    does **not** propagate so the rest of the app keeps starting; the UI
    surfaces the failure via ``/graph/knowledge/health``.
    """
    global _engine, _home, _last_init_error, _stale_warning, _last_enabled

    graphrag_cfg = getattr(cfg, "graphrag", None)
    if graphrag_cfg is None or not getattr(graphrag_cfg, "enabled", False):
        log.info("[graphrag] disabled in config")
        _last_init_error = None
        _stale_warning = None
        _last_enabled = False
        return
    _last_enabled = True

    try:
        await _initialize_engine(cfg, graphrag_cfg)
    except Exception as exc:  # noqa: BLE001
        # Catch-all so any import / model-load failure surfaces to the UI
        # via /graph/knowledge/health instead of being lost in lifespan logs.
        # _resolve_embedder's GraphRAGConfigError is already caught inside
        # _initialize_engine; this picks up everything else.
        _last_init_error = f"{type(exc).__name__}: {exc}"
        log.exception("[graphrag] init failed")


async def _initialize_engine(cfg: Any, graphrag_cfg: Any) -> None:
    """Inner init body, separated so initialize() can blanket-catch failures."""
    global _engine, _home, _last_init_error, _stale_warning

    if _engine is not None:
        try:
            _engine.close()
        except Exception:
            log.warning("[graphrag] failed to close previous engine", exc_info=True)
        _engine = None

    # Reset the builtin extractor singleton so its prototype embeddings
    # get rebuilt from the (possibly updated) ontology on the next call.
    # Cheap: the singleton itself just holds the spaCy pipelines and a
    # dict of cached vectors; reset throws away the vectors only.
    try:
        from nexus.agent import builtin_extractor as _be
        _be._instance = None
    except Exception:
        pass

    from loom.store.graphrag import GraphRAGConfig, GraphRAGEngine

    _home = get_home()
    db_dir = _home / "graphrag"
    db_dir.mkdir(parents=True, exist_ok=True)

    _get_manifest(db_dir)

    emb_cfg = graphrag_cfg.embeddings

    # Resolve the *effective* embedder identifier so stale detection works
    # for the common config pattern of leaving ``embeddings.model`` blank
    # (which means "use the builtin"). Storing the empty string in the
    # manifest would let any later upgrade of the builtin go undetected.
    effective_embedder = _effective_embedder_id(cfg, graphrag_cfg)

    # Stale-embedder detection: compare the embedder used to build the
    # current vector store with what we're about to load. Mismatch
    # doesn't block init (reads still return whatever's there) but warns
    # the user — the geometry change makes new vs old vectors incomparable.
    _stale_warning = None
    previous_embedder = _get_index_meta("embedder_model")
    if previous_embedder and previous_embedder != effective_embedder:
        _stale_warning = (
            f"embedder changed: index built with {previous_embedder!r} but "
            f"config now uses {effective_embedder!r}. Existing vectors are stale; "
            "run `uv run nexus graphrag reindex` to rebuild."
        )
        log.warning("[graphrag] %s", _stale_warning)
        try:
            from ..server.event_bus import publish
            publish({
                "type": "graphrag.stale",
                "previous": previous_embedder,
                "current": effective_embedder,
            })
        except Exception:
            pass
    _set_index_meta("embedder_model", effective_embedder)

    try:
        embedder = _resolve_embedder(cfg, graphrag_cfg)
    except GraphRAGConfigError as exc:
        _last_init_error = str(exc)
        log.error("[graphrag] init failed: %s", exc)
        return

    from loom.store.graphrag import (
        EmbeddingConfig,
        ExtractionConfig,
        OntologyConfig,
    )

    # Ontology source of truth is the vault (~/.nexus/vault/_system/ontology/).
    # Seed it from config defaults the first time around, then read through
    # the store for entity_types / core_relations / allow_custom_relations.
    from nexus.agent.builtin_extractor import RELATION_PROTOTYPES, TYPE_PROTOTYPES
    from nexus.agent.ontology_store import OntologyStore

    vault_root = _home / "vault"
    store = OntologyStore(vault_root)
    cfg_ont = graphrag_cfg.ontology
    if not store.exists():
        store.seed_if_empty(
            entity_types=cfg_ont.entity_types,
            core_relations=cfg_ont.core_relations,
            allow_custom_relations=cfg_ont.allow_custom_relations,
            type_prototypes=TYPE_PROTOTYPES,
            relation_prototypes=RELATION_PROTOTYPES,
        )
    try:
        snapshot = store.load()
    except (FileNotFoundError, ValueError) as exc:
        log.error("[graphrag] failed to load ontology from vault: %s", exc)
        # Fall back to config-provided values so init still succeeds.
        ontology_cfg = OntologyConfig(
            entity_types=cfg_ont.entity_types,
            core_relations=cfg_ont.core_relations,
            allow_custom_relations=cfg_ont.allow_custom_relations,
        )
    else:
        ontology_cfg = OntologyConfig(
            entity_types=snapshot.type_names(),
            core_relations=snapshot.relation_names(),
            allow_custom_relations=snapshot.allow_custom_relations,
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
    _last_init_error = None
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


async def _index_vault_file_tracked(path: str, content: str) -> None:
    """Index one file; record the failure in recent-errors / event_bus on exception.

    Replaces the previous silent variant — failures used to vanish into
    daemon logs only. Now they surface to the UI via SSE
    (``graphrag.index_failed``) and the ``/graph/knowledge/recent-errors``
    endpoint.
    """
    try:
        await index_vault_file(path, content)
    except Exception as exc:
        log.warning("[graphrag] failed to index %s: %s", path, exc, exc_info=True)
        _record_error(path, exc)


async def _index_vault_file_bounded(path: str, content: str) -> None:
    """Same as ``_index_vault_file_tracked`` but gated on a semaphore.

    Prevents a vault-edit storm from queuing dozens of simultaneous Ollama
    extraction calls, which would saturate the httpx connection pool and
    make unrelated LLM-backed endpoints freeze.
    """
    global _index_semaphore
    if _index_semaphore is None:
        _index_semaphore = asyncio.Semaphore(_INDEX_CONCURRENCY)
    async with _index_semaphore:
        await _index_vault_file_tracked(path, content)


def schedule_index(path: str, content: str) -> None:
    """Fire-and-forget GraphRAG indexing of a single vault file.

    Schedules ``index_vault_file`` on the running event loop so callers
    in synchronous code (``vault.write_file``) don't block on LLM/embedding
    calls. Failures are recorded (UI-visible) instead of swallowed.
    Concurrent indexing is capped via :data:`_INDEX_CONCURRENCY`.
    """
    if _engine is None:
        return
    if not content:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_index_vault_file_bounded(path, content))


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
                    await _index_vault_file_tracked(entry.path, content)
            except Exception as exc:
                log.warning("[graphrag] failed to read %s", entry.path, exc_info=True)
                _record_error(entry.path, exc)
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
    global _engine, _stale_warning
    _stale_warning = None
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
