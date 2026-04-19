"""Auto-routing: pick the best model for a given message."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config_file import NexusConfig

ROUTE_TRACE: list[str] = []

_CODING_RE = re.compile(
    r"\b(def |class |function |import |return |SELECT |FROM |UPDATE |DELETE |"
    r"stack ?trace|traceback|null pointer|segfault|compile error|regex|=>|lambda|"
    r"async def|await )"
)
_REASONING_RE = re.compile(
    r"\b(why|explain|reason|think through|plan|strategy|design|compare|tradeoff|"
    r"pros and cons|architecture|decide)\b",
    re.IGNORECASE,
)


def _classify(message: str) -> str:
    if _CODING_RE.search(message):
        return "coding"
    if _REASONING_RE.search(message) and len(message) > 40:
        return "reasoning"
    # 3+ lines with leading whitespace + non-space = code block
    indented = sum(1 for line in message.splitlines() if line and line[0] in " \t" and line.strip())
    if indented >= 3:
        return "coding"
    if len(message) < 80:
        return "trivial"
    return "balanced"


def _score(model, category: str) -> tuple[int, int]:
    s = model.strengths
    if category == "coding":
        primary = s.coding
    elif category == "reasoning":
        primary = s.reasoning
    elif category == "trivial":
        primary = s.speed
    else:  # balanced
        primary = (s.reasoning + s.coding) // 2
    return (primary, s.cost)


def choose_model(message: str, cfg: "NexusConfig") -> str:
    if cfg.agent.routing_mode != "auto":
        return cfg.agent.default_model

    category = _classify(message)
    best_model = None
    best_score = (-1, -1)

    for model in cfg.models:
        sc = _score(model, category)
        if sc > best_score:
            best_score = sc
            best_model = model

    result = best_model.id if best_model else cfg.agent.default_model
    reason = f"category={category}, score={best_score}"
    ROUTE_TRACE.append(f"[router] {result} ({reason})")
    return result
