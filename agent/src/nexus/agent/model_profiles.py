"""Auto-suggest a model's tier from its name.

A cheap, synchronous heuristic for the Add-model flow: users shouldn't have to
guess capability sliders. We map a substring of the model name to one of
fast | balanced | heavy. Unknown names default to balanced — the user can
override in the UI/CLI.
"""

from __future__ import annotations

import re
from typing import Literal

Tier = Literal["fast", "balanced", "heavy"]


# Ordered: first matching pattern wins. Fast-markers (mini/haiku/flash) come
# BEFORE heavy tokens so names like "o3-mini" land in fast, not heavy.
_PATTERNS: list[tuple[str, Tier]] = [
    # Fast / small / cheap — checked first so size suffixes beat family names.
    (r"haiku", "fast"),
    (r"mini", "fast"),
    (r"flash", "fast"),
    (r"nano", "fast"),
    (r"\b[0-9]+\.?[0-9]*b\b", "fast"),  # e.g. 7b, 8b, 1.5b
    (r"phi-?[23]", "fast"),
    (r"gemma", "fast"),
    # Heavy reasoning / large
    (r"\bopus\b", "heavy"),
    (r"\bo1\b", "heavy"),
    (r"\bo3\b", "heavy"),
    (r"405b", "heavy"),
    (r"\bultra\b", "heavy"),
    # Balanced
    (r"sonnet", "balanced"),
    (r"gpt-4o(?!-mini)", "balanced"),
    (r"gpt-4\.1", "balanced"),
    (r"gpt-4\b", "balanced"),
    (r"llama-?3", "balanced"),
    (r"qwen", "balanced"),
    (r"mistral", "balanced"),
    (r"deepseek", "balanced"),
]


def suggest_tier(model_name: str) -> Tier:
    """Pick a tier for a model name. Default balanced when no pattern matches."""
    name = (model_name or "").lower()
    for pattern, tier in _PATTERNS:
        if re.search(pattern, name):
            return tier
    return "balanced"


def suggestion_source(model_name: str) -> str:
    """Return 'heuristic' if a pattern matched, 'default' otherwise."""
    name = (model_name or "").lower()
    for pattern, _ in _PATTERNS:
        if re.search(pattern, name):
            return "heuristic"
    return "default"
