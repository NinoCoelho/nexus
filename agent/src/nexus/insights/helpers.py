"""Low-level helper functions shared across the insights package."""

from __future__ import annotations

from typing import Any


def _format_duration(seconds: float) -> str:
    """Format a duration into a compact human-readable string."""
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        hours = seconds / 3600
        return f"{hours:.1f}h"
    days = seconds / 86400
    return f"{days:.1f}d"


def _extract_tool_name(tc: Any) -> str | None:
    """Pull the tool name from one entry of a ``tool_calls`` JSON array.

    Nexus persists tool calls via ``ToolCall.model_dump()``, producing
    ``{"id": ..., "name": ..., "arguments": {...}}``. We also accept the
    OpenAI wire shape (``{"function": {"name": ...}}``) so imported
    sessions from other tools still get counted.
    """
    if not isinstance(tc, dict):
        return None
    name = tc.get("name")
    if isinstance(name, str) and name:
        return name
    fn = tc.get("function")
    if isinstance(fn, dict):
        fn_name = fn.get("name")
        if isinstance(fn_name, str) and fn_name:
            return fn_name
    return None


def _to_epoch(ts: Any) -> int:
    """Convert a timestamp to a Unix epoch integer.

    Accepts integers (legacy schema) and ISO strings (loom schema).
    Returns 0 on failure.
    """
    if ts is None:
        return 0
    if isinstance(ts, (int, float)):
        return int(ts)
    try:
        from datetime import datetime as _dt
        s = str(ts).replace("T", " ")
        dt = _dt.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        import calendar
        return calendar.timegm(dt.timetuple())
    except Exception:
        return 0


def _bar(width: int, count: int, peak: int) -> str:
    """ASCII bar for day-of-week / hour histograms."""
    if peak <= 0 or count <= 0:
        return ""
    return "█" * max(1, int(count / peak * width))
