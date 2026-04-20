"""Session analytics engine for Nexus.

Adapted from Hermes' ``agent/insights.py`` for Nexus's slimmer session
schema. Unlike Hermes — which tracks input/output/cache tokens, cost,
source platform, and billing metadata per session — Nexus currently
stores only ``(id, title, context, created_at, updated_at)`` on
``sessions`` and ``(role, content, tool_calls, tool_call_id, created_at)``
on ``messages``. We aggregate what's there:

* Session counts + activity by day-of-week and hour
* Messages per role (user / assistant / tool)
* Tool usage — extracted from the ``tool_calls`` JSON on assistant
  messages (Nexus stores its own shape, ``[{id, name, arguments}]``,
  not OpenAI's ``{function: {name}}``; see ``_extract_tool_name``)
* Top sessions by message count + by tool-call count
* Activity streaks

Token/cost breakdowns are intentionally omitted until the session
schema learns to capture ``usage`` from provider responses. When that
lands, pricing can be slotted in via the ``cost`` hooks left empty
in :meth:`InsightsEngine.generate`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────

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


def _bar(width: int, count: int, peak: int) -> str:
    """ASCII bar for day-of-week / hour histograms."""
    if peak <= 0 or count <= 0:
        return ""
    return "\u2588" * max(1, int(count / peak * width))


# ── Engine ─────────────────────────────────────────────────────────────

class InsightsEngine:
    """Analyze session history and produce a usage report.

    Works directly with Nexus's ``SessionStore`` database. Instantiated
    per call — no long-lived state.
    """

    def __init__(self, db_path: Any) -> None:
        from pathlib import Path
        self._db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ── Top-level report ────────────────────────────────────────────

    def generate(self, days: int = 30) -> dict[str, Any]:
        """Generate a complete insights report for the last ``days`` days."""
        cutoff = int(time.time()) - days * 86400

        with self._connect() as conn:
            sessions = self._get_sessions(conn, cutoff)
            message_stats = self._get_message_stats(conn, cutoff)
            tool_usage = self._get_tool_usage(conn, cutoff)
            per_session_msg_count = self._get_per_session_counts(conn, cutoff)

        if not sessions:
            return {
                "days": days,
                "empty": True,
                "overview": {},
                "tools": [],
                "activity": {},
                "top_sessions": [],
                "generated_at": time.time(),
            }

        overview = self._compute_overview(sessions, message_stats, per_session_msg_count)
        tools = self._compute_tool_breakdown(tool_usage)
        activity = self._compute_activity_patterns(sessions)
        top_sessions = self._compute_top_sessions(sessions, per_session_msg_count, tool_usage)

        return {
            "days": days,
            "empty": False,
            "overview": overview,
            "tools": tools,
            "activity": activity,
            "top_sessions": top_sessions,
            "generated_at": time.time(),
        }

    # ── SQL ─────────────────────────────────────────────────────────

    def _get_sessions(
        self, conn: sqlite3.Connection, cutoff: int
    ) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at "
            "FROM sessions WHERE created_at >= ? ORDER BY created_at DESC",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]

    def _get_message_stats(
        self, conn: sqlite3.Connection, cutoff: int
    ) -> dict[str, int]:
        row = conn.execute(
            """SELECT
                 COUNT(*) AS total,
                 SUM(CASE WHEN m.role = 'user'      THEN 1 ELSE 0 END) AS user,
                 SUM(CASE WHEN m.role = 'assistant' THEN 1 ELSE 0 END) AS assistant,
                 SUM(CASE WHEN m.role = 'tool'      THEN 1 ELSE 0 END) AS tool
               FROM messages m
               JOIN sessions s ON s.id = m.session_id
               WHERE s.created_at >= ?""",
            (cutoff,),
        ).fetchone()
        if not row:
            return {"total": 0, "user": 0, "assistant": 0, "tool": 0}
        return {
            "total": row["total"] or 0,
            "user": row["user"] or 0,
            "assistant": row["assistant"] or 0,
            "tool": row["tool"] or 0,
        }

    def _get_tool_usage(
        self, conn: sqlite3.Connection, cutoff: int
    ) -> dict[str, int]:
        """Return a mapping ``tool_name -> call_count`` by walking
        the ``tool_calls`` JSON on assistant messages."""
        rows = conn.execute(
            """SELECT m.session_id, m.tool_calls
               FROM messages m
               JOIN sessions s ON s.id = m.session_id
               WHERE s.created_at >= ?
                 AND m.role = 'assistant'
                 AND m.tool_calls IS NOT NULL""",
            (cutoff,),
        ).fetchall()

        counts: Counter[str] = Counter()
        per_session: dict[str, int] = defaultdict(int)
        for r in rows:
            try:
                tcs = json.loads(r["tool_calls"])
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(tcs, list):
                continue
            for tc in tcs:
                name = _extract_tool_name(tc)
                if name:
                    counts[name] += 1
                    per_session[r["session_id"]] += 1
        # Attach per-session counts to the returned dict so the
        # top_sessions computation can use them without re-querying.
        result = dict(counts)
        result["_per_session"] = per_session  # type: ignore[assignment]
        return result

    def _get_per_session_counts(
        self, conn: sqlite3.Connection, cutoff: int
    ) -> dict[str, int]:
        rows = conn.execute(
            """SELECT s.id, COUNT(m.seq) AS n
               FROM sessions s
               LEFT JOIN messages m ON m.session_id = s.id
               WHERE s.created_at >= ?
               GROUP BY s.id""",
            (cutoff,),
        ).fetchall()
        return {r["id"]: r["n"] or 0 for r in rows}

    # ── Aggregates ──────────────────────────────────────────────────

    def _compute_overview(
        self,
        sessions: list[dict[str, Any]],
        message_stats: dict[str, int],
        per_session_msg_count: dict[str, int],
    ) -> dict[str, Any]:
        total = len(sessions)
        msg_total = sum(per_session_msg_count.values())

        durations = []
        for s in sessions:
            start, end = s.get("created_at"), s.get("updated_at")
            if start and end and end > start:
                durations.append(end - start)
        total_seconds = sum(durations)
        avg_duration = (total_seconds / len(durations)) if durations else 0

        started_ts = [s["created_at"] for s in sessions if s.get("created_at")]
        date_start = min(started_ts) if started_ts else None
        date_end = max(started_ts) if started_ts else None

        return {
            "total_sessions": total,
            "total_messages": msg_total,
            "user_messages": message_stats["user"],
            "assistant_messages": message_stats["assistant"],
            "tool_messages": message_stats["tool"],
            "avg_messages_per_session": (msg_total / total) if total else 0,
            "total_active_seconds": total_seconds,
            "avg_session_duration": avg_duration,
            "date_range_start": date_start,
            "date_range_end": date_end,
        }

    def _compute_tool_breakdown(
        self, tool_usage: dict[str, int]
    ) -> list[dict[str, Any]]:
        # Strip the synthetic `_per_session` key we stashed in _get_tool_usage.
        counts = {k: v for k, v in tool_usage.items() if not k.startswith("_")}
        total = sum(counts.values())
        return [
            {
                "tool": name,
                "count": count,
                "percentage": (count / total * 100) if total else 0,
            }
            for name, count in sorted(
                counts.items(), key=lambda kv: (-kv[1], kv[0])
            )
        ]

    def _compute_activity_patterns(
        self, sessions: list[dict[str, Any]]
    ) -> dict[str, Any]:
        day_counts: Counter[int] = Counter()
        hour_counts: Counter[int] = Counter()
        daily: Counter[str] = Counter()

        for s in sessions:
            ts = s.get("created_at")
            if not ts:
                continue
            dt = datetime.fromtimestamp(ts)
            day_counts[dt.weekday()] += 1
            hour_counts[dt.hour] += 1
            daily[dt.strftime("%Y-%m-%d")] += 1

        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        by_day = [
            {"day": day_names[i], "count": day_counts.get(i, 0)}
            for i in range(7)
        ]
        by_hour = [
            {"hour": i, "count": hour_counts.get(i, 0)}
            for i in range(24)
        ]

        busiest_day = max(by_day, key=lambda x: x["count"]) if by_day else None
        busiest_hour = max(by_hour, key=lambda x: x["count"]) if by_hour else None
        active_days = len(daily)

        # Longest consecutive-day streak.
        max_streak = 0
        if daily:
            all_dates = sorted(daily.keys())
            current = 1
            max_streak = 1
            for i in range(1, len(all_dates)):
                d1 = datetime.strptime(all_dates[i - 1], "%Y-%m-%d")
                d2 = datetime.strptime(all_dates[i], "%Y-%m-%d")
                if (d2 - d1).days == 1:
                    current += 1
                    max_streak = max(max_streak, current)
                else:
                    current = 1

        return {
            "by_day": by_day,
            "by_hour": by_hour,
            "busiest_day": busiest_day,
            "busiest_hour": busiest_hour,
            "active_days": active_days,
            "max_streak": max_streak,
        }

    def _compute_top_sessions(
        self,
        sessions: list[dict[str, Any]],
        per_session_msg_count: dict[str, int],
        tool_usage: dict[str, int],
    ) -> list[dict[str, Any]]:
        top: list[dict[str, Any]] = []
        per_session_tool = tool_usage.get("_per_session", {})  # type: ignore[arg-type]

        if sessions:
            # Most messages
            mm = max(
                sessions,
                key=lambda s: per_session_msg_count.get(s["id"], 0),
            )
            mm_count = per_session_msg_count.get(mm["id"], 0)
            if mm_count > 0:
                top.append({
                    "label": "Most messages",
                    "session_id": mm["id"][:16],
                    "title": (mm.get("title") or "Untitled")[:40],
                    "value": f"{mm_count} msgs",
                    "date": datetime.fromtimestamp(mm["created_at"]).strftime("%b %d")
                    if mm.get("created_at") else "?",
                })

            # Longest duration (created_at → updated_at)
            def _dur(s: dict[str, Any]) -> int:
                start, end = s.get("created_at") or 0, s.get("updated_at") or 0
                return max(0, end - start)

            longest = max(sessions, key=_dur)
            if _dur(longest) > 0:
                top.append({
                    "label": "Longest session",
                    "session_id": longest["id"][:16],
                    "title": (longest.get("title") or "Untitled")[:40],
                    "value": _format_duration(_dur(longest)),
                    "date": datetime.fromtimestamp(longest["created_at"]).strftime("%b %d")
                    if longest.get("created_at") else "?",
                })

            # Most tool calls
            if per_session_tool:
                mt_id = max(per_session_tool, key=lambda sid: per_session_tool[sid])
                mt_count = per_session_tool[mt_id]
                mt = next((s for s in sessions if s["id"] == mt_id), None)
                if mt and mt_count > 0:
                    top.append({
                        "label": "Most tool calls",
                        "session_id": mt["id"][:16],
                        "title": (mt.get("title") or "Untitled")[:40],
                        "value": f"{mt_count} calls",
                        "date": datetime.fromtimestamp(mt["created_at"]).strftime("%b %d")
                        if mt.get("created_at") else "?",
                    })

        return top


# ── Pretty-printer for CLI ─────────────────────────────────────────────

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
