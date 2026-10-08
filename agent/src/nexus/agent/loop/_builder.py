"""Factory for the loom.Agent instance used by Nexus's Agent façade.

Isolated here to keep agent.py under 300 LOC.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import loom.types as lt
from loom.loop import Agent as LoomAgent, AgentConfig

from ..prompt_builder import build_system_prompt
from ...skills.registry import SkillRegistry
from .compactor import NexusCompactor
from .helpers import DEFAULT_MAX_TOOL_ITERATIONS, _AFFIRMATIVES, _NEGATIVES
from .overflow import (
    OUTPUT_HEADROOM_TOKENS as _OVERFLOW_OUTPUT_HEADROOM,
    TOOLS_AND_SYSTEM_OVERHEAD as _OVERFLOW_TOOLS_OVERHEAD,
)

if TYPE_CHECKING:
    from loom.home import AgentHome
    from loom.permissions import AgentPermissions

log = logging.getLogger(__name__)


def _measure_call_overhead(tool_reg: Any, skill_registry: Any, home: Any) -> None:
    """Measure the real per-call system-prompt + tool-schema cost.

    Every budget calculation (``usable_tokens``, overflow detection, the soft
    auto-compact trigger, the pre-turn gate) subtracts this from the model's
    window. It was a hardcoded 12,000 that under-counted a default install by
    roughly 10,000 tokens, so every one of those calculations believed there
    was more room than there was.

    Deliberately measured with no project and no coordinator block: the result
    is a floor, which is how the constant was already documented. Per-session
    extras land in the history estimate instead. Best-effort — on any failure
    the constant stays in force.
    """
    import json

    from .overflow import estimate_tokens, set_measured_overhead, tools_and_system_overhead
    from .overflow import TOOLS_AND_SYSTEM_OVERHEAD

    # Measure once per process. Every sub-agent and the sweep agent construct
    # their own Agent, and re-walking the prompt files on each one is pure
    # waste; ``invalidate_measured_overhead`` (called when MCP tools change)
    # is what re-opens the measurement.
    if tools_and_system_overhead() != TOOLS_AND_SYSTEM_OVERHEAD:
        return

    try:
        specs = tool_reg.specs()
    except Exception:  # noqa: BLE001 — no specs(), keep the constant
        log.debug("overhead measurement: registry exposes no specs()", exc_info=True)
        return
    try:
        schema_text = json.dumps(
            [
                {
                    "name": s.name,
                    "description": getattr(s, "description", "") or "",
                    "parameters": getattr(s, "parameters", {}) or {},
                }
                for s in specs
            ],
            ensure_ascii=False,
            default=str,
        )
        prompt = build_system_prompt(skill_registry, home=home)
        total = estimate_tokens(
            [
                _OverheadProbe(schema_text),
                _OverheadProbe(prompt),
            ]
        )
        set_measured_overhead(total)
        log.info(
            "per-call overhead measured: ~%d tokens (%d tool schemas, prompt %d chars)",
            total, len(specs), len(prompt),
        )
    except Exception:  # noqa: BLE001 — never block agent construction
        log.debug("overhead measurement failed; keeping the default", exc_info=True)


class _OverheadProbe:
    """Minimal message-shaped object for the token estimator."""

    __slots__ = ("content", "tool_calls", "role")

    def __init__(self, content: str) -> None:
        self.content = content
        self.tool_calls = None
        self.role = None


def build_loom_agent(
    *,
    nexus_provider: Any,
    registry: SkillRegistry,
    handlers: Any,
    provider_registry: Any | None,
    get_nexus_cfg: Any,
    get_chosen_model: Any,
    get_turn_trace: Any,
    on_trace_event: Any,
    home: "AgentHome | None" = None,
    permissions: "AgentPermissions | None" = None,
    tool_allowlist: set[str] | None = None,
) -> LoomAgent:
    """Build and return the configured loom.Agent.

    Parameters are callbacks/closures provided by the owning Agent so this
    factory stays free of circular references to Agent itself.

    ``get_nexus_cfg`` is a zero-arg callable that returns the *current* config
    object.  All closures below call it on every access so they always see the
    latest values — no stale references after a config hot-reload.
    """
    from .._loom_bridge import LoomProviderAdapter, build_tool_registry

    # Per-call output cap: per-model override > AgentConfig.default_max_output_tokens
    # > 0 (provider-specific fallback). Resolved at call time so config edits
    # don't require a registry rebuild.
    def _model_max_output_tokens(model_id: str | None) -> int:
        nexus_cfg = get_nexus_cfg()
        if not nexus_cfg:
            return 0
        if model_id:
            for entry in getattr(nexus_cfg, "models", None) or []:
                if entry.id == model_id or entry.model_name == model_id:
                    v = int(getattr(entry, "max_output_tokens", 0) or 0)
                    if v > 0:
                        return v
        return int(getattr(getattr(nexus_cfg, "agent", None), "default_max_output_tokens", 0) or 0)

    _init_cfg = get_nexus_cfg()
    adapter = LoomProviderAdapter(
        nexus_provider,
        provider_registry=provider_registry,
        default_model=getattr(getattr(_init_cfg, "agent", None), "default_model", None)
        if _init_cfg
        else None,
        max_tokens_for=_model_max_output_tokens,
    )
    tool_reg = build_tool_registry(
        skill_registry=registry,
        handlers=handlers,
        search_cfg=_init_cfg.search if _init_cfg else None,
        scrape_cfg=_init_cfg.scrape if _init_cfg else None,
        home=home,
        permissions=permissions,
        tool_allowlist=tool_allowlist,
    )

    # Only the unrestricted registry describes what a normal call costs. The
    # sweep agent's allowlisted registry is a fraction of the size, and this
    # value is process-global — measuring from it would tell every other
    # session it had ~10K more room than it really has.
    if tool_allowlist is None:
        _measure_call_overhead(tool_reg, registry, home)

    max_iter = (
        getattr(_init_cfg.agent, "max_iterations", None) if _init_cfg else None
    ) or DEFAULT_MAX_TOOL_ITERATIONS

    def _choose_model(messages: list[lt.ChatMessage]) -> str | None:
        nexus_cfg = get_nexus_cfg()
        if not nexus_cfg:
            return None
        chosen = get_chosen_model()
        if chosen:
            return chosen
        default = getattr(nexus_cfg.agent, "default_model", None)
        return default

    _iter_counter: list[int] = [0]

    def _before_llm_call(messages: list[lt.ChatMessage]) -> list[lt.ChatMessage]:
        _iter_counter[0] += 1
        on_trace_event("iter", {"n": _iter_counter[0]})
        nexus_cfg = get_nexus_cfg()
        language = getattr(getattr(nexus_cfg, "ui", None), "language", None) if nexus_cfg else None
        project = None
        coordinator = False
        try:
            from ..context import CURRENT_SESSION_ID
            from ...server.project_store import ProjectStore
            from ...home import sessions_db

            session_id = CURRENT_SESSION_ID.get()
            if session_id:
                ps = ProjectStore(sessions_db())
                project = ps.get_project_for_session(session_id)
                from ...coordinator import get_service

                svc = get_service()
                coordinator = bool(svc is not None and svc.is_coordinator(session_id))
        except Exception:
            pass
        sys_prompt = build_system_prompt(
            registry, home=home, language=language, project=project, coordinator=coordinator
        )
        from ..context import TOOL_BUDGET_EXCEEDED
        from .budget import BUDGET_EXCEEDED_HINT

        if TOOL_BUDGET_EXCEEDED.get(False):
            sys_prompt += BUDGET_EXCEEDED_HINT

        # History SYSTEM messages are dropped so stale prompts from earlier
        # turns don't stack — but compaction stores its session-memory summary
        # (and the hard-trim elision marker) as SYSTEM messages, and those are
        # *carried context*, not prompts. Stripping them meant summarization
        # shrank the history while the model never received the summary: from
        # the model's side, compaction was silent truncation.
        #
        # They are folded into the single built prompt rather than passed
        # through as extra SYSTEM messages, because the providers disagree on
        # how to handle more than one (the Anthropic encoder keeps only the
        # last, so a second SYSTEM message would replace the whole prompt).
        from .compact import is_carried_context

        carried = [
            m.content
            for m in messages
            if m.role == lt.Role.SYSTEM and is_carried_context(m.content)
        ]
        if carried:
            sys_prompt = sys_prompt + "\n\n" + "\n\n".join(carried)

        return [
            lt.ChatMessage(role=lt.Role.SYSTEM, content=sys_prompt),
            *[m for m in messages if m.role != lt.Role.SYSTEM],
        ]

    def _limit_msg(n: int) -> str:
        return ""

    def _on_after_turn(turn: Any) -> None:
        on_trace_event("reply", {"text": (turn.reply or "")[:200]})
        _iter_counter[0] = 0

    def _model_context_window(model_id: str) -> int:
        nexus_cfg = get_nexus_cfg()
        if nexus_cfg and getattr(nexus_cfg, "models", None):
            for entry in nexus_cfg.models:
                if entry.id == model_id or entry.model_name == model_id:
                    cw = int(getattr(entry, "context_window", 0) or 0)
                    if cw > 0:
                        return cw
        from .overflow import effective_context_window

        # Never 0: loom's overflow detection must run for models without an
        # explicit window. ``effective_context_window`` resolves the registry
        # and the family-prefix fallbacks, then the conservative default.
        return effective_context_window(model_id)

    loom_cfg = AgentConfig(
        max_iterations=max_iter,
        model=getattr(getattr(_init_cfg, "agent", None), "default_model", None)
        if _init_cfg
        else None,
        choose_model=_choose_model if _init_cfg else None,
        before_llm_call=_before_llm_call,
        on_event=on_trace_event,
        on_after_turn=_on_after_turn,
        serialize_event=lambda ev: ev.model_dump(),
        limit_message_builder=_limit_msg,
        affirmatives=_AFFIRMATIVES,
        negatives=_NEGATIVES,
        context_window=_model_context_window,
        # Single source of truth — nexus's own soft auto-compact trigger
        # budgets against the same numbers, so the meter, the soft trigger and
        # loom's hard limit all describe the same usable window.
        overflow_output_headroom=_OVERFLOW_OUTPUT_HEADROOM,
        overflow_tools_overhead=_OVERFLOW_TOOLS_OVERHEAD,
        compactor=NexusCompactor(nexus_provider),
        max_compaction_attempts=3,
    )
    graphrag_engine = None
    if _init_cfg:
        from ..graphrag_manager import build_graphrag_for_agent

        graphrag_engine = build_graphrag_for_agent(_init_cfg)
    return LoomAgent(
        provider=adapter,
        tool_registry=tool_reg,
        config=loom_cfg,
        graphrag=graphrag_engine,
    )
