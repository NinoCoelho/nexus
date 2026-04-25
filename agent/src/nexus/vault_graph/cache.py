"""Cache state and invalidation for vault_graph."""

from __future__ import annotations

import time
from typing import Any

_CACHE_TTL = 60.0
_cache: tuple[float, Any] | None = None


def invalidate_cache() -> None:
    global _cache
    _cache = None


def get_cache() -> Any | None:
    """Return cached GraphData if still valid, else None."""
    global _cache
    if _cache is not None and time.monotonic() < _cache[0]:
        return _cache[1]
    return None


def set_cache(data: Any) -> None:
    global _cache
    _cache = (time.monotonic() + _CACHE_TTL, data)
