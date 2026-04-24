"""kanban_query agent tool — cross-board card search.

Walks every kanban board in the vault and returns matching cards. All filter
parameters are optional and AND-combined; an empty query lists every card up
to ``limit``. Use this to answer "what am I working on?" / "what's due this
week?" across the whole vault without naming each board.
"""

from __future__ import annotations

import json
from typing import Any

from ..agent.llm import ToolSpec

KANBAN_QUERY_TOOL = ToolSpec(
    name="kanban_query",
    description=(
        "Search every kanban board in the vault for cards matching the given "
        "filters. Returns hits as {path, board_title, lane_id, lane_title, "
        "card_id, title, body, due, priority, labels, assignees, status, "
        "session_id}. All filters optional; AND-combined. Date filters use "
        "ISO 'YYYY-MM-DD'. text is case-insensitive substring."
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Substring match against title + body + labels."},
            "label": {"type": "string", "description": "Cards must have this label."},
            "assignee": {"type": "string", "description": "Cards must be assigned to this name."},
            "priority": {
                "type": "string",
                "enum": ["low", "med", "high", "urgent"],
                "description": "Match cards with this priority.",
            },
            "status": {
                "type": "string",
                "enum": ["running", "done", "failed"],
                "description": "Match cards with this run status.",
            },
            "due_before": {"type": "string", "description": "ISO date — cards due on/before this."},
            "due_after": {"type": "string", "description": "ISO date — cards due on/after this."},
            "lane": {"type": "string", "description": "Lane id or title (matches both)."},
            "limit": {"type": "integer", "description": "Max hits (default 100)."},
        },
    },
)


def handle_kanban_query_tool(args: dict[str, Any]) -> str:
    from .. import vault_kanban

    kwargs = {
        k: args[k]
        for k in ("text", "label", "assignee", "priority", "status", "due_before", "due_after", "lane")
        if k in args and args[k] not in (None, "")
    }
    limit = int(args.get("limit") or 100)
    try:
        hits = vault_kanban.query_boards(limit=limit, **kwargs)
    except (ValueError, OSError) as exc:
        return json.dumps({"ok": False, "error": str(exc)})
    return json.dumps({"ok": True, "hits": hits, "count": len(hits)})
