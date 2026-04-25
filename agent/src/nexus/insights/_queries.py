"""SQL query helpers for InsightsEngine.

Each function takes a live sqlite3.Connection and returns plain dicts/lists.
No business logic here — just data retrieval.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from typing import Any

from .helpers import _extract_tool_name


def get_sessions(conn: sqlite3.Connection, cutoff: str) -> list[dict[str, Any]]:
    """Fetch all sessions created at or after *cutoff* (ISO string).

    Guards against pre-migration DBs where the token/model columns may
    not exist yet — falls back to the minimal column set.
    """
    try:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at, model, "
            "input_tokens, output_tokens, tool_call_count "
            "FROM sessions WHERE created_at >= ? ORDER BY created_at DESC",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at "
            "FROM sessions WHERE created_at >= ? ORDER BY created_at DESC",
            (cutoff,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_message_stats(conn: sqlite3.Connection, cutoff: str) -> dict[str, int]:
    """Count messages by role for all sessions at or after *cutoff*."""
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


def get_message_stats_for(
    conn: sqlite3.Connection, cutoff: str, session_ids: set[str]
) -> dict[str, int]:
    """Count messages by role restricted to *session_ids*."""
    if not session_ids:
        return {"total": 0, "user": 0, "assistant": 0, "tool": 0}
    placeholders = ",".join("?" * len(session_ids))
    row = conn.execute(
        f"""SELECT
             COUNT(*) AS total,
             SUM(CASE WHEN m.role = 'user'      THEN 1 ELSE 0 END) AS user,
             SUM(CASE WHEN m.role = 'assistant' THEN 1 ELSE 0 END) AS assistant,
             SUM(CASE WHEN m.role = 'tool'      THEN 1 ELSE 0 END) AS tool
           FROM messages m
           JOIN sessions s ON s.id = m.session_id
           WHERE s.created_at >= ? AND s.id IN ({placeholders})""",
        (cutoff, *session_ids),
    ).fetchone()
    if not row:
        return {"total": 0, "user": 0, "assistant": 0, "tool": 0}
    return {
        "total": row["total"] or 0,
        "user": row["user"] or 0,
        "assistant": row["assistant"] or 0,
        "tool": row["tool"] or 0,
    }


def _parse_tool_rows(rows: list[Any]) -> dict[str, Any]:
    """Shared parser for tool_call rows → {tool_name: count, _per_session: {...}}."""
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
    result: dict[str, Any] = dict(counts)
    result["_per_session"] = per_session
    return result


def get_tool_usage(conn: sqlite3.Connection, cutoff: str) -> dict[str, Any]:
    """Return ``{tool_name: call_count, _per_session: {sid: count}}`` for all sessions."""
    rows = conn.execute(
        """SELECT m.session_id, m.tool_calls
           FROM messages m
           JOIN sessions s ON s.id = m.session_id
           WHERE s.created_at >= ?
             AND m.role = 'assistant'
             AND m.tool_calls IS NOT NULL""",
        (cutoff,),
    ).fetchall()
    return _parse_tool_rows(rows)


def get_tool_usage_for(
    conn: sqlite3.Connection, cutoff: str, session_ids: set[str]
) -> dict[str, Any]:
    """Return tool usage restricted to *session_ids*."""
    if not session_ids:
        return {"_per_session": {}}
    placeholders = ",".join("?" * len(session_ids))
    rows = conn.execute(
        f"""SELECT m.session_id, m.tool_calls
           FROM messages m
           JOIN sessions s ON s.id = m.session_id
           WHERE s.created_at >= ?
             AND s.id IN ({placeholders})
             AND m.role = 'assistant'
             AND m.tool_calls IS NOT NULL""",
        (cutoff, *session_ids),
    ).fetchall()
    return _parse_tool_rows(rows)


def get_per_session_counts(conn: sqlite3.Connection, cutoff: str) -> dict[str, int]:
    """Return ``{session_id: message_count}`` for all sessions at or after *cutoff*."""
    rows = conn.execute(
        """SELECT s.id, COUNT(m.seq) AS n
           FROM sessions s
           LEFT JOIN messages m ON m.session_id = s.id
           WHERE s.created_at >= ?
           GROUP BY s.id""",
        (cutoff,),
    ).fetchall()
    return {r["id"]: r["n"] or 0 for r in rows}
