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
    nexus_cfg: Any | None,
    get_chosen_model: Any,
    get_turn_trace: Any,
    on_trace_event: Any,
    home: "AgentHome | None" = None,
    permissions: "AgentPermissions | None" = None,
) -> LoomAgent:
    """Build and return the configured loom.Agent.

    Parameters are callbacks/closures provided by the owning Agent so this
    factory stays free of circular references to Agent itself.
    """
    from .._loom_bridge import LoomProviderAdapter, build_tool_registry

    adapter = LoomProviderAdapter(
        nexus_provider,
        provider_registry=provider_registry,
        default_model=getattr(
            getattr(nexus_cfg, "agent", None), "default_model", None
        ) if nexus_cfg else None,
    )
    tool_reg = build_tool_registry(
        skill_registry=registry,
        handlers=handlers,
        search_cfg=nexus_cfg.search if nexus_cfg else None,
        scrape_cfg=nexus_cfg.scrape if nexus_cfg else None,
        home=home,
        permissions=permissions,
    )

    max_iter = (
        getattr(nexus_cfg.agent, "max_iterations", None)
        if nexus_cfg else None
    ) or DEFAULT_MAX_TOOL_ITERATIONS

    def _choose_model(messages: list[lt.ChatMessage]) -> str | None:
        if not nexus_cfg:
            return None
        # If model_id was already set (from app.py routing logic), pass it through.
        # Otherwise fall back to default_model.
        chosen = get_chosen_model()
        if chosen:
            return chosen
        default = getattr(nexus_cfg.agent, "default_model", None)
        return default

    _iter_counter: list[int] = [0]

    def _before_llm_call(messages: list[lt.ChatMessage]) -> list[lt.ChatMessage]:
        _iter_counter[0] += 1
        on_trace_event("iter", {"n": _iter_counter[0]})
        sys_prompt = build_system_prompt(registry, home=home)
        return [
            lt.ChatMessage(role=lt.Role.SYSTEM, content=sys_prompt),
            *[m for m in messages if m.role != lt.Role.SYSTEM],
        ]

    def _limit_msg(n: int) -> str:
        # Keep this empty: the UI replaces the last assistant message
        # with an interactive Continue/Stop banner on `limit_reached`.
        # A non-empty string would briefly flash before the banner swap.
        return ""

    def _on_after_turn(turn: Any) -> None:
        on_trace_event("reply", {"text": (turn.reply or "")[:200]})
        # Reset iter counter for next turn
        _iter_counter[0] = 0

    loom_cfg = AgentConfig(
        max_iterations=max_iter,
        model=getattr(getattr(nexus_cfg, "agent", None), "default_model", None)
        if nexus_cfg else None,
        choose_model=_choose_model if nexus_cfg else None,
        before_llm_call=_before_llm_call,
        on_event=on_trace_event,
        on_after_turn=_on_after_turn,
        serialize_event=lambda ev: ev.model_dump(),
        limit_message_builder=_limit_msg,
        affirmatives=_AFFIRMATIVES,
        negatives=_NEGATIVES,
    )
    graphrag_engine = None
    if nexus_cfg:
        from ..graphrag_manager import build_graphrag_for_agent
        graphrag_engine = build_graphrag_for_agent(nexus_cfg)
    return LoomAgent(
        provider=adapter,
        tool_registry=tool_reg,
        config=loom_cfg,
        graphrag=graphrag_engine,
    )
