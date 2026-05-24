"""Factory for the loom.Agent instance used by Nexus's Agent façade.

Isolated here to keep agent.py under 300 LOC.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import loom.types as lt
from loom.loop import Agent as LoomAgent, AgentConfig

from ..prompt_builder import build_system_prompt
from ...skills.registry import SkillRegistry
from .helpers import DEFAULT_MAX_TOOL_ITERATIONS, _AFFIRMATIVES, _NEGATIVES

if TYPE_CHECKING:
    from loom.home import AgentHome
    from loom.permissions import AgentPermissions


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
        return int(getattr(
            getattr(nexus_cfg, "agent", None), "default_max_output_tokens", 0
        ) or 0)

    _init_cfg = get_nexus_cfg()
    adapter = LoomProviderAdapter(
        nexus_provider,
        provider_registry=provider_registry,
        default_model=getattr(
            getattr(_init_cfg, "agent", None), "default_model", None
        ) if _init_cfg else None,
        max_tokens_for=_model_max_output_tokens,
    )
    from nexus.features import get_features
    _features = get_features()
    tool_reg = build_tool_registry(
        skill_registry=registry,
        handlers=handlers,
        search_cfg=_init_cfg.search if _init_cfg else None,
        scrape_cfg=_init_cfg.scrape if _init_cfg else None,
        home=home,
        permissions=permissions,
        features=_features,
    )
    tool_reg._nexus_features = frozenset(_features)

    max_iter = (
        getattr(_init_cfg.agent, "max_iterations", None)
        if _init_cfg else None
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
        sys_prompt = build_system_prompt(registry, home=home, language=language)
        from ..context import TOOL_BUDGET_EXCEEDED
        from .budget import BUDGET_EXCEEDED_HINT
        if TOOL_BUDGET_EXCEEDED.get(False):
            sys_prompt += BUDGET_EXCEEDED_HINT
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
        from .overflow import known_context_window
        fallback = known_context_window(model_id)
        return fallback if fallback > 0 else 0

    loom_cfg = AgentConfig(
        max_iterations=max_iter,
        model=getattr(getattr(_init_cfg, "agent", None), "default_model", None)
        if _init_cfg else None,
        choose_model=_choose_model if _init_cfg else None,
        before_llm_call=_before_llm_call,
        on_event=on_trace_event,
        on_after_turn=_on_after_turn,
        serialize_event=lambda ev: ev.model_dump(),
        limit_message_builder=_limit_msg,
        affirmatives=_AFFIRMATIVES,
        negatives=_NEGATIVES,
        context_window=_model_context_window,
        overflow_output_headroom=8192,
        overflow_tools_overhead=12_000,
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
