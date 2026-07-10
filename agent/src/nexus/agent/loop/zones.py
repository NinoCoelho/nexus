"""Zone classification — thin re-export from loom.

The canonical implementation lives in ``loom.loop.compaction`` (moved there
from nexus in Phase 1 of the compaction-contract work). This shim preserves
the historical ``nexus.agent.loop.zones`` import path for the call sites that
predate the move; new code should import from loom directly.
"""

from __future__ import annotations

from loom.loop.compaction import (  # noqa: F401 — re-export
    Zone,
    classify_zone,
    zone_thresholds,
)
