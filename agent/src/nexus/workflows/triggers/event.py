"""Event trigger driver — subscribes to the internal event bus.

Listens for published events (vault.indexed, vault.created, vault.removed,
session.completed, etc.) and matches them against workflow event trigger
configurations. Matching events dispatch workflow runs.
"""

from __future__ import annotations

import asyncio
import logging
import datetime
from fnmatch import fnmatch
from typing import Any

from nexus.server.event_bus import subscribe, unsubscribe
from ..models import TriggerType
from ..store import WorkflowStore

log = logging.getLogger(__name__)

_ENGINE_REF: Any = None


def set_engine_ref(engine: Any) -> None:
    global _ENGINE_REF
    _ENGINE_REF = engine


def _get_engine() -> Any:
    return _ENGINE_REF


class EventTriggerListener:
    def __init__(self, store: WorkflowStore) -> None:
        self._store = store
        self._queue = subscribe()
        self._task: asyncio.Task | None = None
        self._registrations: list[dict[str, Any]] = []

    async def start(self) -> None:
        self._task = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        unsubscribe(self._queue)

    def register(self, workflow_path: str, trigger_id: str, event_pattern: str, filters: dict[str, Any] | None = None) -> None:
        self._registrations.append({
            "workflow_path": workflow_path,
            "trigger_id": trigger_id,
            "event_pattern": event_pattern,
            "filters": filters or {},
        })

    def unregister(self, workflow_path: str, trigger_id: str) -> None:
        self._registrations = [
            r for r in self._registrations
            if not (r["workflow_path"] == workflow_path and r["trigger_id"] == trigger_id)
        ]

    async def _consume(self) -> None:
        while True:
            try:
                event = await self._queue.get()
                await self._handle_event(event)
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("event trigger listener error")

    async def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type", "")
        for reg in self._registrations:
            if not fnmatch(event_type, reg["event_pattern"]):
                continue
            if not self._matches_filters(event, reg["filters"]):
                continue
            payload = {
                "event_type": event_type,
                "event_data": {k: v for k, v in event.items() if k != "type"},
                "trigger_id": reg["trigger_id"],
                "fired_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            engine = _get_engine()
            if engine:
                try:
                    await engine.run_workflow(
                        workflow_path=reg["workflow_path"],
                        trigger_id=reg["trigger_id"],
                        trigger_type=TriggerType.event,
                        trigger_payload=payload,
                    )
                except Exception:
                    log.exception("event trigger: failed to dispatch %s", reg["workflow_path"])

    def _matches_filters(self, event: dict[str, Any], filters: dict[str, Any]) -> bool:
        for key, pattern in filters.items():
            val = event.get(key)
            if val is None:
                return False
            if isinstance(pattern, str):
                if not fnmatch(str(val), pattern):
                    return False
            elif pattern != val:
                return False
        return True
