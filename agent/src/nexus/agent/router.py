"""Auto-routing: use the classification model to pick the best model for a given message."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config_file import NexusConfig

log = logging.getLogger(__name__)

ROUTE_TRACE: list[str] = []

_CLASSIFICATION_SYSTEM = """\
You are a model router. Given a user message and a list of available models with their \
capability scores (0-10), select the single best model for this task. Balance capability \
with cost-efficiency — don't pick an expensive model for a trivial task.

Available models:
{models_block}

Respond with ONLY the model id, nothing else."""


async def classify_route(
    message: str,
    cfg: NexusConfig,
    provider_registry: Any | None = None,
) -> str:
    if not cfg.agent.classification_model or not provider_registry:
        return _fallback(message, cfg)

    models_block = "\n".join(
        f"- {m.id}: speed={m.strengths.speed}, cost={m.strengths.cost}, "
        f"reasoning={m.strengths.reasoning}, coding={m.strengths.coding}"
        for m in cfg.models
    )
    prompt = _CLASSIFICATION_SYSTEM.format(models_block=models_block)

    try:
        provider, upstream = provider_registry.get_for_model(cfg.agent.classification_model)
        from .llm import ChatMessage as CM, Role
        messages = [
            CM(role=Role.SYSTEM, content=prompt),
            CM(role=Role.USER, content=message[:500]),
        ]
        result = await provider.chat(messages, model=upstream)
        picked = (result.content or "").strip().strip("`\"'")
        valid = {m.id for m in cfg.models}
        if picked in valid:
            ROUTE_TRACE.append(f"[router] {picked} (llm-classified)")
            return picked
        log.warning("[router] classification model returned unknown model %r, falling back", picked)
    except Exception:
        log.warning("[router] classification call failed", exc_info=True)

    return _fallback(message, cfg)


def _fallback(message: str, cfg: NexusConfig) -> str:
    if cfg.agent.default_model:
        return cfg.agent.default_model
    if cfg.models:
        return cfg.models[0].id
    return ""
