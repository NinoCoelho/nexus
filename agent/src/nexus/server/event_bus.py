"""Process-wide best-effort event bus for vault/index events.

Used to push notifications to the UI when background indexing
(FTS, metadata, GraphRAG) completes for a vault file. Subscribers
attach an :class:`asyncio.Queue` and consume JSON events.

Best-effort: events are dropped for slow consumers (queue full) so a
stuck client cannot stall publishers. The bus has no persistence.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

_QUEUE_MAX = 256
_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
_loop: asyncio.AbstractEventLoop | None = None


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Record the server's event loop so sync publishers can dispatch into it."""
    global _loop
    _loop = loop


def subscribe() -> asyncio.Queue[dict[str, Any]]:
    q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_MAX)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue[dict[str, Any]]) -> None:
    _subscribers.discard(q)


def publish(event: dict[str, Any]) -> None:
    """Publish an event from any thread. Drops if no loop is registered."""
    if not _subscribers:
        return
    loop = _loop
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
    loop.call_soon_threadsafe(_dispatch, event)


def _dispatch(event: dict[str, Any]) -> None:
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            log.debug("event_bus: dropping event for full subscriber queue")
