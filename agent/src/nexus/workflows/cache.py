from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_CACHE_PATH = Path.home() / ".nexus" / ".workflow_cache.json"


class WorkflowListCache:
    """In-memory + disk-persisted cache for workflow summaries (path → summary dict).

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
        if self._warm:
            return True
        with self._lock:
            if self._cache:
                return True
            self._load_disk_locked()
            return bool(self._cache)

    def get_all(self) -> list[dict]:
        with self._lock:
            if not self._cache:
                self._load_disk_locked()
            return list(self._cache.values())

    def get(self, path: str) -> Optional[dict]:
        with self._lock:
            return self._cache.get(path)

    def invalidate(self, path: str) -> None:
        with self._lock:
            self._cache.pop(path, None)
            self._persist_locked()

    def update(self, path: str, summary: dict) -> None:
        with self._lock:
            self._cache[path] = summary
            self._warm = True
            self._persist_locked()

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._warm = False
            try:
                if _CACHE_PATH.exists():
                    _CACHE_PATH.unlink()
            except Exception:
                pass

    def warm(self, summaries: list[dict]) -> None:
        with self._lock:
            self._cache.clear()
            for s in summaries:
                self._cache[s["path"]] = s
            self._warm = True
            self._persist_locked()

    def _load_disk_locked(self) -> None:
        try:
            if not _CACHE_PATH.exists():
                return
            raw = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            items = raw.get("workflows")
            if isinstance(items, list):
                for s in items:
                    if isinstance(s, dict) and "path" in s:
                        self._cache[s["path"]] = s
                if self._cache:
                    self._warm = True
        except Exception:
            log.debug("workflow cache: disk load failed", exc_info=True)

    def _persist_locked(self) -> None:
        try:
            _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = _CACHE_PATH.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({"workflows": list(self._cache.values())}),
                encoding="utf-8",
            )
            tmp.replace(_CACHE_PATH)
        except Exception:
            log.debug("workflow cache: disk persist failed", exc_info=True)
