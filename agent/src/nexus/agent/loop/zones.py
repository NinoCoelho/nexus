from __future__ import annotations

from typing import Literal

Zone = Literal["green", "yellow", "orange", "red"]

_GREEN_THRESHOLD = 0.60
_YELLOW_THRESHOLD = 0.80
_ORANGE_THRESHOLD = 0.90


def classify_zone(tokens_used: int, context_window: int, *, tools_overhead: int = 0) -> Zone:
    effective = context_window - tools_overhead
    if effective <= 0:
        return "red"
    pct = tokens_used / effective
    if pct < _GREEN_THRESHOLD:
        return "green"
    if pct < _YELLOW_THRESHOLD:
        return "yellow"
    if pct < _ORANGE_THRESHOLD:
        return "orange"
    return "red"


def zone_thresholds() -> dict[str, float]:
    return {
        "green": _GREEN_THRESHOLD,
        "yellow": _YELLOW_THRESHOLD,
        "orange": _ORANGE_THRESHOLD,
    }
