"""RSS/Atom feed trigger driver — polls a feed and dispatches one workflow run per new item."""

from __future__ import annotations

import asyncio
import logging
import re
import datetime
from typing import Any

import feedparser

from ..models import TriggerType
from ..store import WorkflowStore

log = logging.getLogger(__name__)

_ENGINE_REF: Any = None


def set_engine_ref(engine: Any) -> None:
    global _ENGINE_REF
    _ENGINE_REF = engine


def _get_engine() -> Any:
    return _ENGINE_REF


class RssTriggerDriver:
    def __init__(self, store: WorkflowStore) -> None:
        self._store = store
        self._tasks: dict[str, asyncio.Task] = {}

    async def start(self, workflow_path: str, wf: Any) -> None:
        for t in wf.triggers:
            if t.type != TriggerType.rss:
                continue
            key = f"{workflow_path}:{t.id}"
            if key in self._tasks:
                continue
            self._tasks[key] = asyncio.create_task(
                self._poll_loop(workflow_path, t), name=f"rss:{key}"
            )

    async def stop(self, workflow_path: str, trigger_id: str) -> None:
        key = f"{workflow_path}:{trigger_id}"
        task = self._tasks.pop(key, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def stop_all(self) -> None:
        for key in list(self._tasks):
            task = self._tasks.pop(key)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def _poll_loop(self, workflow_path: str, trigger: Any) -> None:
        url = trigger.rss_url
        interval = trigger.rss_poll_minutes * 60
        max_items = trigger.rss_max_items
        filter_re = None
        if trigger.rss_filter:
            try:
                filter_re = re.compile(trigger.rss_filter, re.IGNORECASE)
            except re.error:
                log.warning("rss trigger %s: invalid filter regex: %s", trigger.id, trigger.rss_filter)

        while True:
            try:
                await self._poll_once(workflow_path, trigger, url, max_items, filter_re)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("rss trigger %s: poll failed for %s", trigger.id, url)
            await asyncio.sleep(interval)

    async def _poll_once(
        self,
        workflow_path: str,
        trigger: Any,
        url: str,
        max_items: int,
        filter_re: re.Pattern | None,
    ) -> None:
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            log.warning("rss trigger %s: failed to parse %s: %s", trigger.id, url, feed.bozo_exception)
            return

        dispatched = 0
        for entry in feed.entries:
            if dispatched >= max_items:
                break

            item_id = entry.get("id") or entry.get("link") or ""
            if not item_id:
                continue

            if self._store.is_rss_seen(trigger.id, item_id):
                continue

            title = entry.get("title", "")
            description = entry.get("summary", "") or entry.get("description", "")

            if filter_re and not filter_re.search(f"{title} {description}"):
                continue

            content_parts: list[str] = []
            if hasattr(entry, "content") and entry.content:
                for c in entry.content:
                    content_parts.append(c.get("value", ""))
            content = "\n".join(content_parts) if content_parts else description

            payload = {
                "title": title,
                "link": entry.get("link", ""),
                "description": description,
                "content": content,
                "author": entry.get("author", ""),
                "published": entry.get("published", ""),
                "id": item_id,
                "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }

            engine = _get_engine()
            if engine:
                try:
                    await engine.run_workflow(
                        workflow_path=workflow_path,
                        trigger_id=trigger.id,
                        trigger_type=TriggerType.rss,
                        trigger_payload=payload,
                    )
                    self._store.mark_rss_seen(trigger.id, item_id)
                    dispatched += 1
                except Exception:
                    log.exception("rss trigger %s: failed to dispatch %s", trigger.id, workflow_path)
