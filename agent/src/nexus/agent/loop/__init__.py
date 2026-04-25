"""Agent tool-calling loop for Nexus — loom.Agent façade.

The iteration logic (tool-call loop, retry, streaming) now lives in
``loom.loop.Agent``.  This module provides a compatibility layer that:

* Keeps the external signature callers (server/app.py, chat.py TUI,
  tests) depend on: ``run_turn(user_message, *, history, context,
  model_id)`` and ``run_turn_stream(...)``.
* Translates between Nexus's types (flat ChatResponse, dict tool args)
  and loom's types (wrapped ChatResponse, string tool args) via
  :mod:`nexus.agent._loom_bridge`.
* Preserves progressive skill disclosure via loom's ``before_llm_call``
  hook, which re-injects a fresh system prompt on every iteration.
* Preserves router tracing via loom's ``choose_model`` hook.
* Exposes ``_trace``, ``_ask_user_handler``, ``_terminal_handler``, and
  ``_resolve_provider`` as attributes so server/app.py can late-bind
  them without changes.

The old loop's module-level helpers ``_extract_pending_question`` and
``_annotate_short_reply`` are kept because tests import them directly.
"""

from .agent import Agent, TraceCallback
from .helpers import (
    DEFAULT_MAX_TOOL_ITERATIONS,
    SKILL_MANAGE_TOOL,
    AgentTurn,
    _AFFIRMATIVES,
    _NEGATIVES,
    _annotate_short_reply,
    _extract_pending_question,
    _from_loom_message,
    _to_loom_message,
)

__all__ = [
    "Agent",
    "AgentTurn",
    "TraceCallback",
    "DEFAULT_MAX_TOOL_ITERATIONS",
    "SKILL_MANAGE_TOOL",
    "_AFFIRMATIVES",
    "_NEGATIVES",
    "_extract_pending_question",
    "_annotate_short_reply",
    "_to_loom_message",
    "_from_loom_message",
]
