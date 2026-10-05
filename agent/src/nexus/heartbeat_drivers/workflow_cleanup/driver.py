"""Periodic workflow run cleanup — heartbeat driver.

Runs every 6 hours (configured in HEARTBEAT.md schedule) and removes
completed/failed/cancelled workflow runs older than 30 days, plus
orphaned step_runs rows. The WorkflowStore is injected at server startup
via :func:`set_store_ref` — driver state is JSON-persisted, so a live
store object can never travel through it.
"""

from __future__ import annotations

import logging
from typing import Any

from loom.heartbeat import HeartbeatDriver, HeartbeatEvent

log = logging.getLogger(__name__)

_STORE_REF: Any = None


def set_store_ref(store: Any) -> None:
    global _STORE_REF
    _STORE_REF = store


class Driver(HeartbeatDriver):
    async def check(self, state: dict[str, Any]) -> tuple[list[HeartbeatEvent], dict[str, Any]]:
        if _STORE_REF is None:
            log.debug("workflow cleanup: store ref not wired yet, skipping tick")
            return [], state

        try:
            deleted = _STORE_REF.cleanup_old_runs(30)
            if deleted:
                log.info("workflow cleanup: removed %d old runs", deleted)
        except Exception:
            log.exception("workflow cleanup failed")

        return [], state
