"""Auto-routing: pick the best model for a given user message.

When a ``classification_model`` is configured the full LLM classifier is
used. When it's absent, the built-in fastembed embedder ranks models by
cosine similarity between the user's message and each model's
``strengths``/``notes``/``tier`` label — no external call required.
"""

from __future__ import annotations

import asyncio
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

_TIER_BIAS = {"fast": 0.02, "balanced": 0.0, "heavy": -0.02}

_HEAVY_HINTS = (
    "prove", "derive", "architect", "design a system", "multi-step",
    "complex", "debug", "refactor the entire", "trade-off", "compare in depth",
)
_FAST_HINTS = (
    "hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "yes", "no",
    "what time", "what day",
)


async def classify_route(
    message: str,
    cfg: NexusConfig,
    provider_registry: Any | None = None,
) -> str:
    if not cfg.models:
        return ""

    if cfg.agent.classification_model and provider_registry:
        picked = await _llm_classify(message, cfg, provider_registry)
        if picked:
            return picked

    picked = await _embedding_classify(message, cfg)
    if picked:
        return picked
    return _fallback(cfg)


async def _llm_classify(
    message: str, cfg: NexusConfig, provider_registry: Any,
) -> str | None:
    lines: list[str] = []
    for m in cfg.models:
        line = f"- {m.id}: tier={m.tier}"
        if m.notes:
            line += f", notes={m.notes!r}"
        if m.tags:
            line += f", tags={','.join(m.tags)}"
        lines.append(line)
    prompt = _CLASSIFICATION_SYSTEM.format(models_block="\n".join(lines))

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
    return None


async def _embedding_classify(message: str, cfg: NexusConfig) -> str | None:
    """Pick the best model by cosine similarity against per-model label docs.

    Cheap tier bias keeps trivial prompts on the ``fast`` tier even when a
    heavy model's notes mention everything under the sun.
    """
    try:
        from .builtin_embedder import get_builtin_embedder
    except Exception:
        return None

    emb = get_builtin_embedder()
    labels = [_model_label(m) for m in cfg.models]

    lower = message.lower()
    hinted_tier: str | None = None
    if any(h in lower for h in _HEAVY_HINTS):
        hinted_tier = "heavy"
    elif any(h in lower for h in _FAST_HINTS) and len(message) < 60:
        hinted_tier = "fast"

    try:
        loop = asyncio.get_running_loop()
        vectors = await loop.run_in_executor(
            None, emb.embed_sync, [message[:500], *labels]
        )
    except Exception:
        log.warning("[router] embedding classifier failed", exc_info=True)
        return None

    if not vectors or len(vectors) < 2:
        return None
    q = vectors[0]
    label_vecs = vectors[1:]
    scores: list[tuple[float, int]] = []
    for i, v in enumerate(label_vecs):
        s = _cosine(q, v) + _TIER_BIAS.get(cfg.models[i].tier, 0.0)
        if hinted_tier and cfg.models[i].tier == hinted_tier:
            s += 0.1
        scores.append((s, i))
    scores.sort(reverse=True)
    picked = cfg.models[scores[0][1]].id
    ROUTE_TRACE.append(f"[router] {picked} (builtin-embedding)")
    return picked


def _model_label(m: Any) -> str:
    parts = [f"tier {m.tier}"]
    if m.notes:
        parts.append(m.notes)
    if m.tags:
        parts.append("tags: " + ", ".join(m.tags))
    return f"{m.id} — " + "; ".join(parts)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


def _fallback(cfg: NexusConfig) -> str:
    if cfg.agent.default_model:
        return cfg.agent.default_model
    if cfg.models:
        return cfg.models[0].id
    return ""
