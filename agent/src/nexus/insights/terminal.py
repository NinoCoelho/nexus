"""Terminal-friendly text rendering of an InsightsEngine report."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .helpers import _bar, _format_duration


def format_terminal(report: dict[str, Any]) -> str:
    """Format the insights report for terminal display.

    Kept free of rich/typer dependencies so the same function can be
    used from the FastAPI route as a fallback text body.
    """
    if report.get("empty"):
        return f"\n  No sessions found in the last {report.get('days', 30)} days.\n"

    o = report["overview"]
    lines: list[str] = []
    days = report["days"]

    lines.append("")
    lines.append(f"  Nexus Insights — last {days} days")
    lines.append("  " + "=" * 56)

    if o.get("date_range_start") and o.get("date_range_end"):
        start_s = datetime.fromtimestamp(o["date_range_start"]).strftime("%b %d, %Y")
        end_s = datetime.fromtimestamp(o["date_range_end"]).strftime("%b %d, %Y")
        lines.append(f"  Period: {start_s} - {end_s}")
        lines.append("")

    # Overview block
    lines.append("  Overview")
    lines.append("  " + "-" * 56)
    lines.append(
        f"  Sessions:        {o['total_sessions']:<8}  "
        f"Messages:        {o['total_messages']}"
    )
    lines.append(
        f"  User msgs:       {o['user_messages']:<8}  "
        f"Assistant msgs:  {o['assistant_messages']}"
    )
    lines.append(
        f"  Tool msgs:       {o['tool_messages']:<8}  "
        f"Avg msgs/sess:   {o['avg_messages_per_session']:.1f}"
    )
    if o["total_active_seconds"] > 0:
        lines.append(
            f"  Active time:    ~{_format_duration(o['total_active_seconds']):<7}  "
            f"Avg session:    ~{_format_duration(o['avg_session_duration'])}"
        )
    # Token + cost line. Only show when we actually captured some
    # usage — sessions created before the schema migration land with
    # zero tokens and we don't want to pretend we measured nothing.
    if o.get("total_tokens", 0) > 0:
        cost_str = f"${o['estimated_cost_usd']:.4f}"
        if o.get("sessions_unpriced", 0):
            cost_str += "*"
        lines.append(
            f"  Input tokens:   {o['total_input_tokens']:>10,}  "
            f"Output tokens:  {o['total_output_tokens']:,}"
        )
        lines.append(
            f"  Total tokens:   {o['total_tokens']:>10,}  "
            f"Est. cost:      {cost_str}"
        )
        if o.get("sessions_unpriced", 0):
            lines.append(
                f"  * {o['sessions_unpriced']} session(s) use models without known pricing"
            )
    lines.append("")

    # Model breakdown
    if report.get("models"):
        lines.append("  Models")
        lines.append("  " + "-" * 56)
        lines.append(f"  {'Model':<30} {'Sess':>5} {'Tokens':>10} {'Cost':>10}")
        for m in report["models"]:
            name = (m["model"] or "unknown")
            # Strip the provider prefix for display (openai/gpt-4o → gpt-4o).
            if "/" in name:
                name = name.split("/")[-1]
            name = name[:30]
            if m.get("has_pricing"):
                cost_cell = f"${m['cost_usd']:.4f}"
            else:
                cost_cell = "N/A"
            lines.append(
                f"  {name:<30} {m['sessions']:>5} "
                f"{m['total_tokens']:>10,} {cost_cell:>10}"
            )
        lines.append("")

    # Tools
    if report["tools"]:
        lines.append("  Top tools")
        lines.append("  " + "-" * 56)
        lines.append(f"  {'Tool':<28} {'Calls':>8} {'Share':>8}")
        for t in report["tools"][:15]:
            lines.append(
                f"  {t['tool']:<28} {t['count']:>8,} {t['percentage']:>7.1f}%"
            )
        if len(report["tools"]) > 15:
            lines.append(f"  ... and {len(report['tools']) - 15} more tools")
        lines.append("")

    # Activity
    act = report.get("activity") or {}
    by_day = act.get("by_day") or []
    if by_day:
        lines.append("  Activity by day")
        lines.append("  " + "-" * 56)
        peak = max((d["count"] for d in by_day), default=0)
        for d in by_day:
            bar = _bar(16, d["count"], peak)
            lines.append(f"  {d['day']}  {bar:<16} {d['count']}")
        lines.append("")

        by_hour = act.get("by_hour") or []
        busy = [h for h in sorted(by_hour, key=lambda x: -x["count"]) if h["count"] > 0][:5]
        if busy:
            parts = []
            for h in busy:
                hr = h["hour"]
                ampm = "AM" if hr < 12 else "PM"
                disp = hr % 12 or 12
                parts.append(f"{disp}{ampm} ({h['count']})")
            lines.append(f"  Peak hours: {', '.join(parts)}")
        if act.get("active_days"):
            lines.append(f"  Active days: {act['active_days']}")
        if act.get("max_streak", 0) > 1:
            lines.append(f"  Best streak: {act['max_streak']} consecutive days")
        lines.append("")

    # Top sessions
    if report.get("top_sessions"):
        lines.append("  Notable sessions")
        lines.append("  " + "-" * 56)
        for ts in report["top_sessions"]:
            title = ts.get("title", "")
            lines.append(
                f"  {ts['label']:<20} {ts['value']:<14} {ts['date']:<8} {title}"
            )
        lines.append("")

    return "\n".join(lines)
