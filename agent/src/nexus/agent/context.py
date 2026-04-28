"""Per-turn contextual state carried through the agent loop.

The `Agent` itself is session-agnostic — it runs a turn against any
message with any history, and doesn't know which `/chat` session
triggered the call. Tools that *do* need that routing information
(currently `ask_user`, `terminal`, and HITL credential prompts)
read it from a `ContextVar` that the server sets at the entry to
each `/chat` call.

``CURRENT_SESSION_ID`` is re-exported from :mod:`loom.hitl.broker`
so that nexus and any loom-native tool (e.g. ``loom.tools.terminal``)
share the same ContextVar object — setting it once in the chat
handler is visible to both sides without double-set ceremony.

Default is None: tests that call `run_turn` without going through
the server don't set it and don't invoke HITL tools. Handlers that
read it must handle the None case with a clear error rather than
crashing.
"""

from __future__ import annotations

from contextvars import ContextVar

from loom.context import (  # noqa: F401 — re-export
    CURRENT_SESSION_ID,
    SUBAGENT_DEPTH,
)

# Tracks the chain of card_ids whose lane-prompts have been auto-dispatched
# *into* the current execution context. Used by the lane-change hook to
# detect cycles (A→B→A) and cap cascade depth so a misconfigured set of
# lane prompts can't infinite-loop. ContextVars copy into asyncio.Tasks
# spawned via create_task, so the chain propagates through nested
# background dispatches without explicit plumbing.
DISPATCH_CHAIN: ContextVar[tuple[str, ...]] = ContextVar(
    "DISPATCH_CHAIN", default=()
)
