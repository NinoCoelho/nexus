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
    from loom.context import CURRENT_USER_ID as CURRENT_USER_ID
except ImportError:
    from contextvars import ContextVar
    CURRENT_USER_ID: ContextVar[str | None] = ContextVar(
        "loom_current_user_id", default=None,
    )

# The model explicitly chosen for the CURRENT turn (UI picker / caller).
# The Agent singleton is shared by every chat session, so this must live in
# task-local context, never on the instance: a second turn launched while
# another is mid-loop would otherwise hijack the running turn's model at the
# next ``choose_model`` evaluation (observed 2026-11-21: a master-chat turn
# running on glm silently switched to an LM Studio model mid-turn because a
# concurrent session's launch overwrote the shared attribute). ``_UNSET``
# distinguishes "this task never chose a model" (fall back to instance state)
# from "this turn explicitly runs on the default" (None must win).
_UNSET_CHOSEN_MODEL = object()
CHOSEN_MODEL: ContextVar[object] = ContextVar(
    "nexus_chosen_model", default=_UNSET_CHOSEN_MODEL,
)


def current_chosen_model(fallback: str | None = None) -> str | None:
    """The turn's explicitly chosen model, or ``fallback`` when unset here."""
    value = CHOSEN_MODEL.get()
    if value is _UNSET_CHOSEN_MODEL:
        return fallback
    return value  # type: ignore[return-value]


def set_chosen_model(model_id: str | None) -> None:
    """Record the calling task's turn model (call at every turn entry)."""
    CHOSEN_MODEL.set(model_id)


# Per-turn compaction-round counter (see compactor.CompactionLoopGuardError).
# Task-local like CHOSEN_MODEL: the compactor is one shared instance, so a
# dict keyed by session id would leak across turns; a list handed out at turn
# entry keeps each turn's count isolated. ``reset_turn_compaction_rounds`` is
# called at the turn entries; readers create lazily so bare call paths
# (tests, sub-agents) still work.
_COMPACTION_ROUNDS: ContextVar[object] = ContextVar(
    "nexus_compaction_rounds", default=None,
)


def turn_compaction_rounds() -> list[int]:
    """The calling task's compaction-round counter (``[count]``, mutable)."""
    value = _COMPACTION_ROUNDS.get()
    if not isinstance(value, list):
        value = [0]
        _COMPACTION_ROUNDS.set(value)
    return value


def reset_turn_compaction_rounds() -> None:
    """Start a fresh compaction-round budget (call at every turn entry)."""
    _COMPACTION_ROUNDS.set([0])

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

# Surface that launched the current turn — "web" | "chrome-extension" |
# "ios" | "telegram" | "coordinator" | "mcp" | "team". Set by the
# ChatTurnRunner from its ``origin`` (contextvars copy into the detached
# task, so tools dispatched inside the turn see it). Read by:
# - the `browser` tool gate (managed CDP Chrome is policy-blocked on
#   side-panel turns),
# - the prompt builder (per-surface standing instructions).
# Empty string = unknown/background context; gates treat it as unrestricted.
TURN_SURFACE: ContextVar[str] = ContextVar("nexus_turn_surface", default="")
