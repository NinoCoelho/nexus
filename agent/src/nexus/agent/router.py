"""Auto-routing: use the classification model to pick the best model for a given message."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config_file import NexusConfig

log = logging.getLogger(__name__)

ROUTE_TRACE: list[str] = []

_CLASSIFICATION_SYSTEM = """\
You are a model router. Given a user message and available models, pick the \
cheapest model that can do the job well. Tiers: `fast` = small/cheap/low-latency \
(greetings, simple lookups, short rewrites); `balanced` = typical chat, coding, \
analysis; `heavy` = hard reasoning, long chains, architecture, complex debugging. \
Prefer fast when the request is trivial. Read each model's `notes` — they may \
list limitations (no tool use, no images) or strengths (language, domain).

Available models:
{models_block}

Respond with ONLY the model id, nothing else."""


async def classify_route(
    message: str,
    cfg: NexusConfig,
    provider_registry: Any | None = None,
) -> str:
    if not cfg.agent.classification_model or not provider_registry:
        return _fallback(cfg)

    lines: list[str] = []
    for m in cfg.models:
        line = f"- {m.id}: tier={m.tier}"
        if m.notes:
            line += f", notes={m.notes!r}"
        if m.tags:
            line += f", tags={','.join(m.tags)}"
        lines.append(line)
    models_block = "\n".join(lines)
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

    return _fallback(cfg)


def _fallback(cfg: NexusConfig) -> str:
    if cfg.agent.default_model:
        return cfg.agent.default_model
    if cfg.models:
        return cfg.models[0].id
    return ""
