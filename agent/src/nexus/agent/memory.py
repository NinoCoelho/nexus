"""MemoryStore singleton lifecycle for Nexus.

Constructs a :class:`loom.store.memory.MemoryStore` backed by the Nexus
vault (via :class:`nexus.vault_provider.NexusVaultProvider`) and
optionally wired to the GraphRAG engine.  The singleton is lazy-initialised
on first access so that it picks up a GraphRAG engine that was started in
the server lifespan.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_store: Any | None = None

_HOME = Path.home() / ".nexus"
_MEMORY_DIR = _HOME / "memory"
_INDEX_DB = _MEMORY_DIR / "_index" / "memory.sqlite"


def get_memory_store() -> Any:
    global _store
    if _store is not None:
        return _store

    from loom.store.memory import MemoryStore
    from ..vault_provider import NexusVaultProvider

    graphrag_engine = None
    try:
        from .graphrag_manager import get_engine
        graphrag_engine = get_engine()
    except Exception:
        pass

    vault_provider = NexusVaultProvider()

    _store = MemoryStore(
        _MEMORY_DIR,
        _INDEX_DB,
        vault_provider=vault_provider,
        vault_prefix="memory",
        graphrag=graphrag_engine,
    )
    log.info(
        "MemoryStore initialized (vault-backed, graphrag=%s)",
        "enabled" if graphrag_engine is not None else "disabled",
    )
    return _store


def close_memory_store() -> None:
    global _store
    if _store is not None:
        _store.close()
        _store = None
