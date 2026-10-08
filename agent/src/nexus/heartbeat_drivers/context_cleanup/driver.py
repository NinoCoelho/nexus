"""Context-artifact retention — heartbeat driver.

Runs daily (see HEARTBEAT.md) and bounds the two directories compaction
writes into: ``vault/.tool-cache/`` and ``vault/.session-memory/.parts/``.

The sweep is pure filesystem work with no store dependency, so unlike
``workflow_cleanup`` this driver needs nothing injected. It is also fast and
bounded, so it runs inline — per the scheduling rules, only long-running work
needs to be detached, and a directory scan of a few thousand files is not
that.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from loom.heartbeat import HeartbeatDriver, HeartbeatEvent

log = logging.getLogger(__name__)


class Driver(HeartbeatDriver):
    async def check(self, state: dict[str, Any]) -> tuple[list[HeartbeatEvent], dict[str, Any]]:
        try:
            from ...context_artifacts import sweep_context_artifacts

            # Filesystem work off the event loop — the tick must stay fast.
            stats = await asyncio.to_thread(sweep_context_artifacts)
            if stats.get("files_removed"):
                state = {
                    **state,
                    "last_removed_files": stats["files_removed"],
                    "last_removed_bytes": stats["bytes_removed"],
                }
        except Exception:
            log.exception("context artifact cleanup failed")

        return [], state
