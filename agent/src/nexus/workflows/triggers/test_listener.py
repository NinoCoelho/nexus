"""Temporary trigger test listeners for verifying trigger configuration.

Provides SSE-based listeners that capture the first matching trigger event
(webhook, fs_watch, or event bus) without executing the workflow.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from fnmatch import fnmatch
from typing import Any

log = logging.getLogger(__name__)

_TEST_LISTENERS: dict[str, dict[str, Any]] = {}


def get_test_listener(test_id: str) -> dict[str, Any] | None:
    return _TEST_LISTENERS.get(test_id)


def remove_test_listener(test_id: str) -> None:
    _TEST_LISTENERS.pop(test_id, None)


class TestTriggerListener:
    def __init__(
        self,
        test_id: str,
        trigger_type: str,
        trigger_config: dict[str, Any],
        store: Any,
        engine: Any,
    ) -> None:
        self.test_id = test_id
        self.trigger_type = trigger_type
        self.config = trigger_config
        self._store = store
        self._engine = engine
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._cleanup_tasks: list[Any] = []

    async def start(self) -> Any:
        _TEST_LISTENERS[self.test_id] = {
            "queue": self._queue,
            "trigger_type": self.trigger_type,
            "cleanup_tasks": self._cleanup_tasks,
        }

        if self.trigger_type == "webhook":
            token = f"test_{uuid.uuid4().hex}"
            import datetime
            self._store.register_webhook_token(
                token,
                self.config.get("workflow_path", ""),
                self.config.get("trigger_id", ""),
                datetime.datetime.now(datetime.timezone.utc).isoformat(),
            )
            _TEST_LISTENERS[self.test_id]["test_token"] = token
            webhook_url = self.config.get("base_url", "") + f"/workflow/trigger/{token}"
            yield _sse_event("test.listening", {"webhook_url": webhook_url, "token": token, "test_id": self.test_id})

        elif self.trigger_type == "fs_watch":
            watch_path = self.config.get("path", "")
            yield _sse_event("test.listening", {"watch_path": watch_path, "test_id": self.test_id})
            task = asyncio.create_task(self._watch_fs(watch_path))
            self._cleanup_tasks.append(task)

        elif self.trigger_type == "event":
            event_pattern = self.config.get("event", "*")
            yield _sse_event("test.listening", {"event_pattern": event_pattern, "test_id": self.test_id})
            task = asyncio.create_task(self._subscribe_event(event_pattern))
            self._cleanup_tasks.append(task)

        else:
            yield _sse_event("test.error", {"message": f"unsupported trigger type: {self.trigger_type}"})
            return

        try:
            payload = await asyncio.wait_for(self._queue.get(), timeout=60)
            yield _sse_event("test.captured", payload)
        except asyncio.TimeoutError:
            yield _sse_event("test.timeout", {"message": "no event received within 60 seconds"})

    async def _watch_fs(self, watch_path: str) -> None:
        if not watch_path:
            await self._queue.put({"error": "no watch path configured"})
            return
        expanded = os.path.expanduser(watch_path)
        if not os.path.isdir(expanded):
            await self._queue.put({"error": f"path does not exist: {watch_path}"})
            return

        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler, FileCreatedEvent

        captured = asyncio.Event()
        listener_ref = self

        class _Handler(FileSystemEventHandler):
            def dispatch(self, event: Any) -> None:
                if event.is_directory:
                    return
                if captured.is_set():
                    return
                captured.set()
                src_path = getattr(event, "src_path", "")
                event_type = "created" if isinstance(event, FileCreatedEvent) else "modified"
                try:
                    loop = asyncio.get_running_loop()
                    loop.call_soon_threadsafe(
                        listener_ref._queue.put_nowait,
                        {"src_path": src_path, "event_type": event_type, "is_directory": False},
                    )
                except Exception:
                    pass

        observer = Observer()
        handler = _Handler()
        observer.schedule(handler, expanded, recursive=True)
        observer.start()
        self._cleanup_tasks.append(observer)

        await captured.wait()
        observer.stop()
        observer.join(timeout=5)

    async def _subscribe_event(self, event_pattern: str) -> None:
        from nexus.server.event_bus import subscribe, unsubscribe

        q = subscribe()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=60)
                except asyncio.TimeoutError:
                    continue
                event_type = event.get("type", "")
                if fnmatch(event_type, event_pattern):
                    await self._queue.put({
                        "event_type": event_type,
                        "event_data": {k: v for k, v in event.items() if k != "type"},
                    })
                    return
        finally:
            unsubscribe(q)

    async def cleanup(self) -> None:
        for task_or_observer in self._cleanup_tasks:
            try:
                if isinstance(task_or_observer, asyncio.Task):
                    task_or_observer.cancel()
                    try:
                        await task_or_observer
                    except asyncio.CancelledError:
                        pass
                else:
                    task_or_observer.stop()
                    task_or_observer.join(timeout=2)
            except Exception:
                pass
        test_info = _TEST_LISTENERS.pop(self.test_id, None)
        if test_info and test_info.get("test_token"):
            try:
                self._store.remove_webhook_tokens(test_info["test_token"])
            except Exception:
                pass


def _sse_event(event_type: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, default=str)
    return f"event: {event_type}\ndata: {payload}\n\n".encode()
