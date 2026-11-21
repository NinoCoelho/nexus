"""Nexus compactor — bridges loom's compaction contract to nexus's existing
tool-shrink + summarize machinery.

Loom's agent loop calls the compactor when it detects context overflow at an
iteration boundary (see ``loom.loop._executor.resolve_overflow``). This module
is the consumer side of that contract: it adapts loom ``ChatMessage`` objects
to nexus ``ChatMessage`` objects, runs the proven
:func:`compact_and_summarize` pipeline (the same one the cross-turn session
guard already uses), and hands the shortened history back to loom.

Escalation by attempt: the first pass is cheap (tool-result shrink only, no
LLM call) because the dominant overflow cause is a single oversized
``vault_read`` / ``web_scrape`` result — tool-shrink alone usually resolves
it. Only if that's insufficient do later attempts invoke the summarizer.

Reasoning-content (``reasoning_content`` on assistant messages from
reasoning-capable models) is round-tripped across the loom↔nexus conversion,
mirroring the manual copy the agent loop does elsewhere — the bridge module
itself is reasoning-blind.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

import loom.types as lt
from loom.loop.compaction import CompactionRequest, CompactionResult

from nexus.agent.context import CURRENT_SESSION_ID, turn_compaction_rounds
from nexus.agent.llm import ChatMessage as NexusChatMessage
from nexus.agent.llm import Role
from nexus.agent.loop.compact import compact_and_summarize

from .._loom_bridge.message import _loom_to_nexus_message, _nexus_to_loom_message

if TYPE_CHECKING:
    from nexus.agent.llm import LLMProvider

log = logging.getLogger(__name__)

# Strategy per attempt number. Attempt 1 stays LLM-free (tools_only); attempt
# 2 adds summarization (auto); attempt 3+ goes aggressive. Bounded by loom's
# ``max_compaction_attempts`` (default 3).
_STRATEGY_BY_ATTEMPT = {1: "tools_only", 2: "auto", 3: "aggressive"}

# Compaction *rounds* (not attempts) allowed within one turn before the loop
# guard refuses. Loom escalates attempts inside a round (tools_only → auto →
# aggressive+guarantee_fit), and ``guarantee_fit`` means a round always ends
# fitting — so a turn that starts yet another round has re-grown its history
# with fresh tool results *after* being force-trimmed. Three rounds means the
# model has lost its own work to hard_trim three times and is re-reading the
# same files: continuing just burns tokens on degraded output (observed
# 2026-11-21: the CFO chat hard-trimmed 23 of 25 messages, re-read, re-trimmed).
# 0 disables the guard.
MAX_COMPACTION_ROUNDS_PER_TURN = 3


class CompactionLoopGuardError(RuntimeError):
    """Raised when a turn needs compaction too many times in a row.

    Loom catches compactor exceptions, emits ``compaction_error`` and stops
    the overflow rescue, so the turn ends with the visible ``context_overflow``
    error (UI shows compact_history / new_session actions) instead of
    silently thrashing token after token.
    """


def _loom_to_nexus_with_reasoning(msg: lt.ChatMessage) -> NexusChatMessage:
    nm = _loom_to_nexus_message(msg)
    if nm.role == Role.ASSISTANT:
        rc = getattr(msg, "_reasoning_content", None)
        if rc:
            try:
                nm.reasoning_content = rc  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 — best-effort, never fatal
                pass
    return nm


def _nexus_to_loom_with_reasoning(msg: NexusChatMessage) -> lt.ChatMessage:
    lm = _nexus_to_loom_message(msg)
    if msg.role == Role.ASSISTANT and getattr(msg, "reasoning_content", None):
        lm._reasoning_content = msg.reasoning_content  # type: ignore[attr-defined]
    return lm


class NexusCompactor:
    """Async callable implementing loom's ``Compactor`` contract.

    Constructed once at agent build time (see ``_builder.build_loom_agent``)
    with the nexus LLM provider, and invoked by loom per overflowing turn.
    Per-turn routing (session id) is read from the ``CURRENT_SESSION_ID``
    contextvar that the chat handler already sets, so the compactor needs no
    per-turn plumbing.
    """

    def __init__(
        self,
        provider: LLMProvider | None = None,
        *,
        model_id: str | None = None,
        model_resolver: Callable[[], str | None] | None = None,
        max_rounds_per_turn: int = MAX_COMPACTION_ROUNDS_PER_TURN,
    ) -> None:
        self._provider = provider
        self._model_id = model_id
        # Resolved at call time so mid-turn compaction summarizes on the same
        # model that is driving the turn. Built without one (the default), the
        # summarizer falls back to ``_call_summarizer``'s config-default
        # resolution — without it, ``provider.chat(model=None)`` raises
        # "No model specified" locally (providers are built with model="")
        # and every compaction degrades to a lossy ``hard_trim``.
        self._model_resolver = model_resolver
        self._max_rounds_per_turn = max_rounds_per_turn

    async def __call__(self, request: CompactionRequest) -> CompactionResult:
        # Loop guard: refuse to keep force-trimming a turn that has already
        # been compacted MAX times. Must run BEFORE the try/except below —
        # the raise has to reach loom (it turns compactor exceptions into a
        # turn-ending ``context_overflow``), not be swallowed here.
        if request.attempt == 1 and self._max_rounds_per_turn > 0:
            rounds = turn_compaction_rounds()
            rounds[0] += 1
            if rounds[0] > self._max_rounds_per_turn:
                session_hint = None
                try:
                    session_hint = CURRENT_SESSION_ID.get()
                except LookupError:
                    session_hint = None
                log.warning(
                    "compaction loop guard: turn on session %s needed %d compaction "
                    "rounds (attempt=%d iteration=%d tokens=%d window=%d) — aborting "
                    "with context_overflow instead of trimming again",
                    (session_hint or "?")[:12],
                    rounds[0] - 1,
                    request.attempt,
                    request.iteration,
                    request.estimated_tokens,
                    request.context_window,
                )
                raise CompactionLoopGuardError(
                    f"Turn needed {rounds[0] - 1} compaction rounds and still outgrew "
                    "its context budget — the model can't make progress in this "
                    "window. Compact the history, start a new session, or switch "
                    "to a model with a larger context window."
                )

        # loom ChatMessage → nexus ChatMessage (preserving reasoning).
        nexus_msgs = [_loom_to_nexus_with_reasoning(m) for m in request.messages]
        if not nexus_msgs:
            return CompactionResult(messages=list(request.messages))

        strategy = _STRATEGY_BY_ATTEMPT.get(request.attempt, "aggressive")
        session_id = None
        try:
            session_id = CURRENT_SESSION_ID.get()
        except LookupError:
            session_id = None

        try:
            compacted, report = await compact_and_summarize(
                nexus_msgs,
                context_window=request.context_window,
                session_id=session_id,
                model_id=(self._model_resolver() if self._model_resolver else None)
                or self._model_id,
                provider=self._provider,
                strategy=strategy,
                # Final attempt must not be able to come back still
                # overflowed: the deterministic trimmer forces a fit even
                # when the summarizer is unavailable.
                guarantee_fit=request.attempt >= 3,
            )
        except Exception:
            log.warning(
                "NexusCompactor: compact_and_summarize failed (attempt %d, "
                "strategy=%s); returning input unchanged",
                request.attempt,
                strategy,
                exc_info=True,
            )
            return CompactionResult(
                messages=list(request.messages),
                still_overflowed=True,
                actions=["error"],
            )

        actions: list[str] = []
        if report.compact_report.compacted > 0:
            actions.append("tool_shrink")
        if report.summarized:
            actions.append("summarize")
        if report.hard_trimmed:
            actions.append("hard_trim")
        if not actions:
            actions.append("noop")

        loom_msgs = [_nexus_to_loom_with_reasoning(m) for m in compacted]
        return CompactionResult(
            messages=loom_msgs,
            tokens_after=report.tokens_after,
            actions=actions,
            still_overflowed=report.still_overflowed,
        )
