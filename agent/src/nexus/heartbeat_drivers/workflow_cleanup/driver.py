"""Periodic workflow run cleanup — heartbeat driver.

Runs every 6 hours (configured in HEARTBEAT.md schedule) and removes
completed/failed/cancelled workflow runs older than 30 days, plus
orphaned step_runs rows.
"""

from __future__ import annotations

import logging
from typing import Any

from loom.heartbeat import HeartbeatDriver, HeartbeatEvent

log = logging.getLogger(__name__)


class Driver(HeartbeatDriver):
    async def check(self, state: dict[str, Any]) -> tuple[list[HeartbeatEvent], dict[str, Any]]:
        store = state.get("_store")
        if store is None:
            return [], state

        try:
            deleted = store.cleanup_old_runs(30)
            if deleted:
                log.info("workflow cleanup: removed %d old runs", deleted)
        except Exception:
            log.exception("workflow cleanup failed")

        return [], state
