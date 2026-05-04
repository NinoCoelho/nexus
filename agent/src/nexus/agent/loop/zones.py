from __future__ import annotations

from typing import Literal

Zone = Literal["green", "yellow", "orange", "red"]

_GREEN_THRESHOLD = 0.60
_YELLOW_THRESHOLD = 0.80
_ORANGE_THRESHOLD = 0.90


def classify_zone(tokens_used: int, context_window: int) -> Zone:
    if context_window <= 0:
        return "green"
    pct = tokens_used / context_window
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
