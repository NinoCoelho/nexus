"""InsightsEngine — analyzes session history and produces a usage report."""

from __future__ import annotations

import logging
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from .helpers import _format_duration, _to_epoch
from ._queries import (get_message_stats, get_message_stats_for,
                       get_per_session_counts, get_sessions,
                       get_tool_usage, get_tool_usage_for)

log = logging.getLogger(__name__)


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

    def generate(self, days: int = 30, model_filter: str | None = None) -> dict[str, Any]:
        """Generate a complete insights report for the last ``days`` days.

        If ``model_filter`` is set, the report is scoped to sessions whose
        ``model`` column matches exactly — useful for "show me what auto picked
        for this specific model over the last week."
        """
        cutoff = int(time.time()) - days * 86400
        # Loom stores created_at as an ISO timestamp string ("YYYY-MM-DD HH:MM:SS").
        # Convert the integer cutoff to the same format so SQLite string comparison works.
        from datetime import datetime as _dt
        cutoff_iso = _dt.utcfromtimestamp(cutoff).strftime("%Y-%m-%d %H:%M:%S")

        with self._connect() as conn:
            sessions = get_sessions(conn, cutoff_iso)
            if model_filter:
                sessions = [s for s in sessions if (s.get("model") or "") == model_filter]
                allowed_ids = {s["id"] for s in sessions}
                message_stats = get_message_stats_for(conn, cutoff_iso, allowed_ids)
                tool_usage = get_tool_usage_for(conn, cutoff_iso, allowed_ids)
                per_session_msg_count = {
                    k: v for k, v in get_per_session_counts(conn, cutoff_iso).items()
                    if k in allowed_ids
                }
            else:
                message_stats = get_message_stats(conn, cutoff_iso)
                tool_usage = get_tool_usage(conn, cutoff_iso)
                per_session_msg_count = get_per_session_counts(conn, cutoff_iso)

        if not sessions:
            return {
                "days": days, "model_filter": model_filter, "empty": True,
                "overview": {}, "tools": [], "activity": {}, "top_sessions": [],
                "generated_at": time.time(),
            }

        overview = self._compute_overview(sessions, message_stats, per_session_msg_count)
        tools = self._compute_tool_breakdown(tool_usage)
        activity = self._compute_activity_patterns(sessions)
        top_sessions = self._compute_top_sessions(sessions, per_session_msg_count, tool_usage)
        models = self._compute_model_breakdown(sessions)

        return {
            "days": days,
            "model_filter": model_filter,
            "empty": False,
            "overview": overview,
            "models": models,
            "tools": tools,
            "activity": activity,
            "top_sessions": top_sessions,
            "generated_at": time.time(),
        }

    # ── Aggregates ──────────────────────────────────────────────────

    def _compute_overview(
        self,
        sessions: list[dict[str, Any]],
        message_stats: dict[str, int],
        per_session_msg_count: dict[str, int],
    ) -> dict[str, Any]:
        from ..usage_pricing import estimate_cost

        total = len(sessions)
        msg_total = sum(per_session_msg_count.values())

        durations = []
        for s in sessions:
            start, end = s.get("created_at"), s.get("updated_at")
            if start and end:
                start_ts = _to_epoch(start)
                end_ts = _to_epoch(end)
                if end_ts > start_ts:
                    durations.append(end_ts - start_ts)
        total_seconds = sum(durations)
        avg_duration = (total_seconds / len(durations)) if durations else 0

        total_input = sum(int(s.get("input_tokens") or 0) for s in sessions)
        total_output = sum(int(s.get("output_tokens") or 0) for s in sessions)

        total_cost = 0.0
        sessions_priced = 0
        sessions_unpriced = 0
        for s in sessions:
            cost, status = estimate_cost(
                s.get("model") or "",
                input_tokens=int(s.get("input_tokens") or 0),
                output_tokens=int(s.get("output_tokens") or 0),
            )
            if status in ("ok", "zero"):
                total_cost += cost or 0.0
                sessions_priced += 1
            else:
                sessions_unpriced += 1
        started_ts = [_to_epoch(s["created_at"]) for s in sessions if s.get("created_at")]
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
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "estimated_cost_usd": total_cost,
            "sessions_priced": sessions_priced,
            "sessions_unpriced": sessions_unpriced,
        }

    def _compute_model_breakdown(
        self, sessions: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Aggregate usage per model slug for the report."""
        from ..usage_pricing import estimate_cost, has_known_pricing

        buckets: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "sessions": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
                "has_pricing": False,
            }
        )
        for s in sessions:
            model = s.get("model") or "unknown"
            b = buckets[model]
            b["sessions"] += 1
            b["input_tokens"] += int(s.get("input_tokens") or 0)
            b["output_tokens"] += int(s.get("output_tokens") or 0)
            b["total_tokens"] = b["input_tokens"] + b["output_tokens"]
            cost, status = estimate_cost(
                model,
                input_tokens=int(s.get("input_tokens") or 0),
                output_tokens=int(s.get("output_tokens") or 0),
            )
            if status in ("ok", "zero") and cost is not None:
                b["cost_usd"] += cost
            b["has_pricing"] = has_known_pricing(model)

        result = [{"model": k, **v} for k, v in buckets.items()]
        result.sort(key=lambda x: (x["total_tokens"], x["sessions"]), reverse=True)
        return result

    def _compute_tool_breakdown(self, tool_usage: dict[str, int]) -> list[dict[str, Any]]:
        counts = {k: v for k, v in tool_usage.items() if not k.startswith("_")}
        total = sum(counts.values())
        return [
            {"tool": name, "count": count, "percentage": (count / total * 100) if total else 0}
            for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
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
            epoch = _to_epoch(ts)
            if epoch == 0:
                continue
            dt = datetime.fromtimestamp(epoch)
            day_counts[dt.weekday()] += 1
            hour_counts[dt.hour] += 1
            daily[dt.strftime("%Y-%m-%d")] += 1

        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        by_day = [{"day": day_names[i], "count": day_counts.get(i, 0)} for i in range(7)]
        by_hour = [{"hour": i, "count": hour_counts.get(i, 0)} for i in range(24)]

        busiest_day = max(by_day, key=lambda x: x["count"]) if by_day else None
        busiest_hour = max(by_hour, key=lambda x: x["count"]) if by_hour else None
        active_days = len(daily)
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

        def _fmt_date(s: dict[str, Any]) -> str:
            return datetime.fromtimestamp(_to_epoch(s["created_at"])).strftime("%b %d") \
                if s.get("created_at") else "?"

        if sessions:
            mm = max(sessions, key=lambda s: per_session_msg_count.get(s["id"], 0))
            mm_count = per_session_msg_count.get(mm["id"], 0)
            if mm_count > 0:
                top.append({"label": "Most messages", "session_id": mm["id"],
                             "title": (mm.get("title") or "Untitled")[:40],
                             "value": f"{mm_count} msgs", "date": _fmt_date(mm)})

            def _dur(s: dict[str, Any]) -> int:
                return max(0, _to_epoch(s.get("updated_at")) - _to_epoch(s.get("created_at")))

            longest = max(sessions, key=_dur)
            if _dur(longest) > 0:
                top.append({"label": "Longest session", "session_id": longest["id"],
                             "title": (longest.get("title") or "Untitled")[:40],
                             "value": _format_duration(_dur(longest)),
                             "date": _fmt_date(longest)})

            if per_session_tool:
                mt_id = max(per_session_tool, key=lambda sid: per_session_tool[sid])
                mt_count = per_session_tool[mt_id]
                mt = next((s for s in sessions if s["id"] == mt_id), None)
                if mt and mt_count > 0:
                    top.append({"label": "Most tool calls", "session_id": mt["id"],
                                 "title": (mt.get("title") or "Untitled")[:40],
                                 "value": f"{mt_count} calls", "date": _fmt_date(mt)})

        return top
