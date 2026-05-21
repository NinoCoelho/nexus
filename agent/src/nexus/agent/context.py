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

try:
    from loom.context import CURRENT_USER_ID as CURRENT_USER_ID  # noqa: F401
except ImportError:
    from contextvars import ContextVar
    CURRENT_USER_ID: ContextVar[str | None] = ContextVar(
        "loom_current_user_id", default=None,
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

# Snapshot of the session history passed to the current turn, as
# Nexus ChatMessage objects. Set by the agent loop before entering
# loom's turn, read by tools like context_status and fork_session
# that need to know the current context size. Empty list by default
# (tests, sub-agents).
CURRENT_HISTORY: ContextVar[list] = ContextVar(
    "CURRENT_HISTORY", default=[]
)

# The effective context window for the current turn. 0 = unknown.
CURRENT_CONTEXT_WINDOW: ContextVar[int] = ContextVar(
    "CURRENT_CONTEXT_WINDOW", default=0
)

# Set to True when the cumulative tool-result token budget is exceeded
# for the current turn. Read by the before_llm_call hook to inject a
# "synthesize now" hint into the system prompt.
TOOL_BUDGET_EXCEEDED: ContextVar[bool] = ContextVar(
    "TOOL_BUDGET_EXCEEDED", default=False
)

# Role-based tool allowlist for the current turn. None = all tools (admin /
# single-user). A frozenset of tool names = only these tools. Set by the
# chat_stream handler from the current user's role before each turn.
ALLOWED_TOOLS: ContextVar[frozenset[str] | None] = ContextVar(
    "ALLOWED_TOOLS", default=None
)

GLOBAL_SCRAPE_COUNT: ContextVar[int] = ContextVar(
    "GLOBAL_SCRAPE_COUNT", default=0
)
