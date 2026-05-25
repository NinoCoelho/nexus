"""Shared utilities for dream phases — JSON parsing, session loading."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def extract_json(text: str) -> dict[str, Any] | None:
    """Parse JSON from LLM output that may be wrapped in markdown fences."""
    if not text:
        return None
    candidate = text.strip()
    if candidate.startswith("```"):
        first_nl = candidate.find("\n")
        if first_nl >= 0:
            candidate = candidate[first_nl + 1:]
        last_fence = candidate.rfind("```")
        if last_fence > 0:
            candidate = candidate[:last_fence]
    candidate = candidate.strip()
    if not candidate:
        return None
    brace = candidate.find("{")
    bracket = candidate.find("[")
    if brace >= 0 and (bracket < 0 or brace <= bracket):
        candidate = candidate[brace:]
    elif bracket >= 0:
        candidate = candidate[bracket:]
    try:
        result = json.loads(candidate)
        if isinstance(result, dict):
            return result
        if isinstance(result, list) and len(result) > 0 and isinstance(result[0], dict):
            return result[0]
    except json.JSONDecodeError:
        pass
    try:
        brace = candidate.rfind("}")
        if brace >= 0:
            result = json.loads(candidate[: brace + 1])
            if isinstance(result, dict):
                return result
    except json.JSONDecodeError:
        pass
    return None


def load_session_summaries(
    *,
    db_path: Path,
    limit: int = 20,
    since: datetime | None = None,
    roles: tuple[str, ...] = ("user", "assistant"),
    max_content_length: int = 2000,
    preview_len: int = 400,
    include_date: bool = False,
) -> list[dict[str, str]]:
    """Load recent session summaries from the sessions SQLite database.

    Parameters
    ----------
    db_path:
        Path to the sessions SQLite file.
    limit:
        Max sessions to return.
    since:
        Only sessions updated after this datetime.
    roles:
        Which message roles to include in the concatenated preview.
    max_content_length:
        Skip messages longer than this (characters).
    preview_len:
        Truncate the concatenated preview to this many characters.
    include_date:
        Whether to include a ``date`` field (first 10 chars of updated_at).
    """
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            role_list = ", ".join(f"'{r}'" for r in roles)
            query = (
                "SELECT s.id, s.title, s.updated_at, "
                "GROUP_CONCAT(m.content, ' ') AS messages "
                "FROM sessions s "
                f"LEFT JOIN messages m ON m.session_id = s.id "
                f"AND m.role IN ({role_list}) "
                f"AND LENGTH(m.content) < {max_content_length} "
                "WHERE COALESCE(s.hidden, 0) = 0 "
            )
            params: list[Any] = []
            if since:
                query += " AND s.updated_at > ?"
                params.append(since.strftime("%Y-%m-%dT%H:%M:%S"))
            query += (
                " GROUP BY s.id ORDER BY s.updated_at DESC LIMIT ?"
            )
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            summaries: list[dict[str, str]] = []
            for r in rows:
                msgs = r["messages"] or ""
                entry: dict[str, str] = {
                    "session_id": r["id"][:8],
                    "title": r["title"] or "Untitled",
                    "preview": msgs[:preview_len],
                }
                if include_date and r["updated_at"]:
                    entry["date"] = r["updated_at"][:10]
                summaries.append(entry)
            return summaries
        finally:
            conn.close()
    except Exception:
        log.exception("dream: session load failed")
        return []
