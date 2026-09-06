"""Vault file watcher — keeps indexes fresh for externally-synced files.

Syncthing/rsync/Obsidian write directly into ``~/.nexus/vault/``, bypassing
``vault.write_file()``'s post-write hooks (FTS, tags/links, graph cache,
GraphRAG). This module runs a watchdog observer on the vault root and,
after a short quiet period, mirrors those hooks for the externally changed
paths: an mtime-incremental rebuild of both index DBs (cheap — unchanged
files are skipped), graph cache invalidation, per-path GraphRAG
scheduling, and event-bus notifications so the UI refreshes.

Debounce strategy: collect candidate paths from watchdog events, wait for
``quiet_s`` seconds with no new events (sync tools deliver bursts), then
flush once. Correctness does not depend on the event stream — the
incremental rebuild rescans mtime/size for the whole vault, so missed or
spurious events only cost one cheap scan, never a wrong index.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SUFFIXES = {".md", ".mdx"}
_TEMP_SUFFIXES = ("~", ".tmp", ".swp")


def _interesting(rel_path: str) -> bool:
    """True if a vault-relative path should trigger a reindex flush."""
    p = Path(rel_path)
    if p.suffix.lower() not in _SUFFIXES:
        return False
    if any(part.startswith(".") for part in p.parts):
        return False
    name = p.name
    return not name.endswith(_TEMP_SUFFIXES)


class _Handler:
    """Collects vault-relative candidate paths from watchdog events."""

    def __init__(self, root: Path, watcher: "VaultWatcher") -> None:
        self._root = root
        self._watcher = watcher

    def dispatch(self, event: Any) -> None:
        if event.is_directory:
            return
        paths = [getattr(event, "src_path", "")]
        dest = getattr(event, "dest_path", "")
        if dest:
            paths.append(dest)
        for raw in paths:
            if not raw:
                continue
            try:
                rel = str(Path(raw).relative_to(self._root))
            except ValueError:
                continue
            if _interesting(rel):
                self._watcher._mark(rel)


class VaultWatcher:
    """Watches the vault tree and re-indexes after external changes."""

    def __init__(
        self,
        vault_root: Path | None = None,
        *,
        quiet_s: float = 2.0,
        poll_s: float = 0.5,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        from .vault import _vault_root

        self._root = Path(vault_root) if vault_root is not None else _vault_root()
        self._quiet_s = quiet_s
        self._poll_s = poll_s
        self._loop = loop
        self._lock = threading.Lock()
        self._dirty: set[str] = set()
        self._last_event = 0.0
        self._stop = threading.Event()
        self._observer: Any = None
        self._flusher: threading.Thread | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        from watchdog.observers import Observer

        self._observer = Observer()
        self._observer.schedule(_Handler(self._root, self), str(self._root), recursive=True)
        self._observer.start()
        self._flusher = threading.Thread(
            target=self._run, name="vault-watch", daemon=True,
        )
        self._flusher.start()
        log.info("vault watcher started on %s", self._root)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._observer is not None:
            try:
                self._observer.stop()
            except Exception:
                pass
        if self._flusher is not None:
            self._flusher.join(timeout=timeout)
        log.info("vault watcher stopped")

    def _run(self) -> None:
        while not self._stop.wait(self._poll_s):
            try:
                self._maybe_flush()
            except Exception:
                log.exception("vault watcher: flush loop error")

    # ── event collection ─────────────────────────────────────────────────────

    def _mark(self, rel: str) -> None:
        with self._lock:
            self._dirty.add(rel)
            self._last_event = time.monotonic()

    def _pending(self) -> set[str] | None:
        """Return dirty paths if the quiet period has elapsed, else None."""
        with self._lock:
            if not self._dirty:
                return None
            if time.monotonic() - self._last_event < self._quiet_s:
                return None
            paths = self._dirty
            self._dirty = set()
        return paths

    # ── flush ────────────────────────────────────────────────────────────────

    def _maybe_flush(self) -> None:
        paths = self._pending()
        if paths is not None:
            self._flush(paths)

    def flush_now(self) -> None:
        """Flush immediately, ignoring the quiet period (tests / manual use)."""
        with self._lock:
            paths = self._dirty
            self._dirty = set()
        self._flush(paths)

    def _flush(self, rel_paths: set[str]) -> None:
        if not rel_paths:
            return
        log.info("vault watcher: %d externally changed path(s), reindexing", len(rel_paths))
        try:
            from . import vault_search
            vault_search.rebuild_from_disk(full=False)
        except Exception:
            log.exception("vault watcher: FTS rebuild failed")
        try:
            from . import vault_index
            vault_index.rebuild_from_disk(full=False)
        except Exception:
            log.exception("vault watcher: tags/links rebuild failed")
        try:
            from . import vault_graph
            vault_graph.invalidate_cache()
        except Exception:
            pass

        for rel in sorted(rel_paths):
            full = self._root / rel
            if full.exists():
                self._handle_updated(rel, full)
            else:
                self._handle_removed(rel)

    def _handle_updated(self, rel: str, full: Path) -> None:
        try:
            content = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        from .vault import _publish_update_events
        _publish_update_events(rel, content)
        self._schedule_graphrag(rel, content)

    def _handle_removed(self, rel: str) -> None:
        try:
            from .server.event_bus import publish
            publish({"type": "vault.removed", "path": rel})
        except Exception:
            pass
        try:
            from .agent.graphrag_manager import remove_source
            remove_source(rel)
        except Exception:
            log.warning("vault watcher: graphrag remove_source failed for %s", rel, exc_info=True)

    def _schedule_graphrag(self, rel: str, content: str) -> None:
        try:
            from .agent.graphrag_manager import schedule_index
        except Exception:
            return
        try:
            loop = self._loop
            if loop is not None and loop.is_running():
                # schedule_index needs a running loop in the calling thread;
                # hop to the server loop from this flusher thread.
                loop.call_soon_threadsafe(schedule_index, rel, content)
            else:
                schedule_index(rel, content)
        except Exception:
            log.warning("vault watcher: graphrag schedule_index failed for %s", rel, exc_info=True)


_default: VaultWatcher | None = None


def start_default(loop: asyncio.AbstractEventLoop | None = None) -> VaultWatcher | None:
    """Start the process-wide watcher (server lifespan).

    Returns None when disabled via NEXUS_DISABLE_VAULT_WATCH (tests and
    troubleshooting).
    """
    import os

    global _default
    if _default is not None:
        return _default
    if os.environ.get("NEXUS_DISABLE_VAULT_WATCH"):
        log.info("vault watcher disabled via NEXUS_DISABLE_VAULT_WATCH")
        return None
    _default = VaultWatcher(loop=loop)
    _default.start()
    return _default


def stop_default() -> None:
    """Stop the process-wide watcher, if running."""
    global _default
    if _default is not None:
        _default.stop()
        _default = None
