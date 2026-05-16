"""Small SSE helpers shared across route modules."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any


async def keepalive(
    source: AsyncIterator[Any],
    interval: float = 20.0,
) -> AsyncIterator[Any]:
    """Yield items from *source*, emitting ``None`` every *interval* seconds
    when the source stalls.

    The underlying ``__anext__`` is **never cancelled** on timeout — the
    pending task keeps running so that long-lived coroutines (e.g. an agent
    turn blocked on ``ask_user``) are not disturbed.
    """
    anext_task: asyncio.Task[Any] | None = None
    try:
        while True:
            if anext_task is None:
                anext_task = asyncio.ensure_future(source.__anext__())
            done, _ = await asyncio.wait({anext_task}, timeout=interval)
            if done:
                anext_task = None
                try:
                    yield done.pop().result()
                except StopAsyncIteration:
                    return
            else:
                yield None
    finally:
        if anext_task is not None:
            anext_task.cancel()
            try:
                await anext_task
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
