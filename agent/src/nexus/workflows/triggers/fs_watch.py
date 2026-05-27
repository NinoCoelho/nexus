"""Filesystem watcher trigger using watchdog.

Monitors configured directories for file events matching glob patterns
and dispatches workflow runs when matching events occur.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileModifiedEvent, FileDeletedEvent, FileMovedEvent
from watchdog.observers import Observer

from ..models import TriggerType, WorkflowDef
from ..store import WorkflowStore
from .base import TriggerDriver

log = logging.getLogger(__name__)


class _WorkflowEventHandler(FileSystemEventHandler):
    def __init__(
        self,
        workflow_path: str,
        trigger_id: str,
        pattern: str,
        events: list[str],
        debounce_ms: int,
        loop: asyncio.AbstractEventLoop,
        store: WorkflowStore,
    ) -> None:
        super().__init__()
        self.workflow_path = workflow_path
        self.trigger_id = trigger_id
        self.pattern = pattern
        self.watched_events = set(events)
        self.debounce_ms = debounce_ms
        self._loop = loop
        self._store = store
        self._pending: dict[str, float] = {}

    def dispatch(self, event: Any) -> None:
        if event.is_directory:
            return
        src_path = getattr(event, "src_path", "")
        if not src_path:
            return
        from fnmatch import fnmatch
        filename = os.path.basename(src_path)
        if not fnmatch(filename, self.pattern):
            return
        event_type = self._map_event(event)
        if event_type not in self.watched_events:
            return
        try:
            self._loop.call_soon_threadsafe(
                self._fire, src_path, event_type,
            )
        except RuntimeError:
            pass

    def _map_event(self, event: Any) -> str:
        if isinstance(event, FileCreatedEvent):
            return "created"
        elif isinstance(event, FileModifiedEvent):
            return "modified"
        elif isinstance(event, FileDeletedEvent):
            return "deleted"
        elif isinstance(event, FileMovedEvent):
            return "moved"
        return "unknown"

    def _fire(self, file_path: str, event_type: str) -> None:
        import time

        now = time.monotonic()
        key = f"{self.workflow_path}:{self.trigger_id}:{file_path}:{event_type}"
        last = self._pending.get(key, 0)
        if now - last < self.debounce_ms / 1000.0:
            return
        self._pending[key] = now

        if self._store.is_fs_seen(self.trigger_id, file_path, event_type):
            return
        self._store.mark_fs_seen(self.trigger_id, file_path, event_type)

        payload = {
            "file_path": file_path,
            "event_type": event_type,
            "trigger_id": self.trigger_id,
        }
        log.info("fs_watch trigger: %s event %s on %s", self.trigger_id, event_type, file_path)

        try:
            engine = _get_engine()
            if engine is None:
                return
            asyncio.create_task(engine.run_workflow(
                workflow_path=self.workflow_path,
                trigger_id=self.trigger_id,
                trigger_type=TriggerType.fs_watch,
                trigger_payload=payload,
            ))
        except Exception:
            log.exception("fs_watch: failed to dispatch workflow run")


_ENGINE_REF: Any = None


def set_engine_ref(engine: Any) -> None:
    global _ENGINE_REF
    _ENGINE_REF = engine


def _get_engine() -> Any:
    return _ENGINE_REF


class FsWatchTriggerDriver(TriggerDriver):
    def __init__(self, store: WorkflowStore) -> None:
        self._store = store
        self._observer = Observer()
        self._observer.start()
        self._watches: dict[str, Any] = {}

    @property
    def trigger_type(self) -> TriggerType:
        return TriggerType.fs_watch

    async def start(self, workflow_path: str, wf: WorkflowDef, trigger_config: Any = None) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is None:
            log.warning("fs_watch: no event loop, skipping watch for %s", workflow_path)
            return

        for trigger in wf.triggers:
            if trigger.type != TriggerType.fs_watch:
                continue
            watch_path = trigger.path
            if watch_path:
                watch_path = os.path.expanduser(watch_path)
            if not watch_path or not os.path.isdir(watch_path):
                log.warning("fs_watch: path %s does not exist or is not a dir", trigger.path)
                continue

            key = f"{workflow_path}:{trigger.id}"
            if key in self._watches:
                continue

            handler = _WorkflowEventHandler(
                workflow_path=workflow_path,
                trigger_id=trigger.id,
                pattern=trigger.pattern,
                events=trigger.events,
                debounce_ms=trigger.debounce_ms,
                loop=loop,
                store=self._store,
            )
            watch = self._observer.schedule(handler, watch_path, recursive=True)
            self._watches[key] = watch
            log.info("fs_watch: watching %s (pattern=%s, events=%s)", watch_path, trigger.pattern, trigger.events)

    async def stop(self, workflow_path: str, trigger_id: str) -> None:
        key = f"{workflow_path}:{trigger_id}"
        watch = self._watches.pop(key, None)
        if watch is not None:
            try:
                self._observer.unschedule(watch)
                log.info("fs_watch: stopped watching %s", key)
            except Exception:
                pass

    def stop_all(self) -> None:
        try:
            self._observer.stop()
            self._observer.join(timeout=5)
        except Exception:
            pass
