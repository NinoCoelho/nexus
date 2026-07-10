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
from typing import TYPE_CHECKING

import loom.types as lt

from nexus.agent.llm import ChatMessage as NexusChatMessage, Role
from nexus.agent.loop.compact import compact_and_summarize
from nexus.agent.context import CURRENT_SESSION_ID

from .._loom_bridge.message import _loom_to_nexus_message, _nexus_to_loom_message
from loom.loop.compaction import CompactionRequest, CompactionResult

if TYPE_CHECKING:
    from nexus.agent.llm import LLMProvider

log = logging.getLogger(__name__)

# Strategy per attempt number. Attempt 1 stays LLM-free (tools_only); attempt
# 2 adds summarization (auto); attempt 3+ goes aggressive. Bounded by loom's
# ``max_compaction_attempts`` (default 3).
_STRATEGY_BY_ATTEMPT = {1: "tools_only", 2: "auto", 3: "aggressive"}


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
    ) -> None:
        self._provider = provider
        self._model_id = model_id

    async def __call__(self, request: CompactionRequest) -> CompactionResult:
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
                model_id=self._model_id,
                provider=self._provider,
                strategy=strategy,
            )
        except Exception:  # noqa: BLE001 — contract says degrading is OK
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
        if not actions:
            actions.append("noop")

        loom_msgs = [_nexus_to_loom_with_reasoning(m) for m in compacted]
        return CompactionResult(
            messages=loom_msgs,
            tokens_after=report.tokens_after,
            actions=actions,
            still_overflowed=report.still_overflowed,
        )
