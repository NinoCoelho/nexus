from __future__ import annotations

import threading
from typing import Optional


class WorkflowListCache:
    """In-memory cache for workflow summaries (path → summary dict).

    Expected summary shape::

        {
            "path": str,          # vault-relative path, e.g. "workflows/my-flow.md"
            "title": str,         # workflow title from frontmatter
            "enabled": bool,      # whether the workflow is active
            "step_count": int,    # number of steps defined
            "trigger_count": int, # number of triggers defined
        }
    """

    def __init__(self) -> None:
        self._cache: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._warm: bool = False

    @property
    def is_warm(self) -> bool:
        return self._warm

    def get_all(self) -> list[dict]:
        with self._lock:
            return list(self._cache.values())

    def get(self, path: str) -> Optional[dict]:
        with self._lock:
            return self._cache.get(path)

    def invalidate(self, path: str) -> None:
        with self._lock:
            self._cache.pop(path, None)

    def update(self, path: str, summary: dict) -> None:
        with self._lock:
            self._cache[path] = summary
            self._warm = True

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._warm = False
