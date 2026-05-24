"""MemoryStore singleton lifecycle for Nexus.

Constructs a :class:`loom.store.memory.MemoryStore` backed by the Nexus
vault (via :class:`nexus.vault_provider.NexusVaultProvider`) and
optionally wired to the GraphRAG engine.  The singleton is lazy-initialised
on first access so that it picks up a GraphRAG engine that was started in
the server lifespan.
"""

from __future__ import annotations

import logging
from typing import Any

from ..home import memory_dir, memory_index_db

log = logging.getLogger(__name__)

_stores: dict[str, Any] = {}


def get_memory_store() -> Any:
    from ..home import _base
    key = str(_base())
    s = _stores.get(key)
    if s is not None:
        return s

    from loom.store.memory import MemoryStore
    from ..vault_provider import NexusVaultProvider

    graphrag_engine = None
    try:
        from .graphrag_manager import get_engine
        graphrag_engine = get_engine()
    except Exception:
        pass

    vault_provider = NexusVaultProvider()

    s = MemoryStore(
        memory_dir(),
        memory_index_db(),
        vault_provider=vault_provider,
        vault_prefix="memory",
        graphrag=graphrag_engine,
    )
    _stores[key] = s
    log.info(
        "MemoryStore initialized (vault-backed, graphrag=%s, home=%s)",
        "enabled" if graphrag_engine is not None else "disabled",
        key,
    )
    return s


def close_memory_store() -> None:
    for s in _stores.values():
        try:
            s.close()
        except Exception:
            pass
    _stores.clear()
