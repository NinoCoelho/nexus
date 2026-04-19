"""Kanban agent tool: kanban_manage."""

from __future__ import annotations

import json
from typing import Any

from ..agent.llm import ToolSpec

KANBAN_MANAGE_TOOL = ToolSpec(
    name="kanban_manage",
    description=(
        "Manage the kanban board. Actions: list, create, move, update, delete. "
        "Use this to track tasks with the user."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "create", "move", "update", "delete"],
                "description": "Action to perform.",
            },
            "id": {"type": "string", "description": "Card ID (required for move/update/delete)."},
            "title": {"type": "string", "description": "Card title (required for create)."},
            "column": {"type": "string", "description": "Column name (todo/doing/done or custom)."},
            "notes": {"type": "string", "description": "Freeform markdown notes body."},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tag list.",
            },
        },
        "required": ["action"],
    },
)


def handle_kanban_tool(args: dict[str, Any]) -> str:
    from .. import kanban

    action = args.get("action", "")
    try:
        if action == "list":
            cards = kanban.list_cards()
            columns = kanban.list_columns()
            return json.dumps({
                "ok": True,
                "columns": columns,
                "cards": [c.to_dict() for c in cards],
            })

        if action == "create":
            title = args.get("title", "")
            if not title:
                return json.dumps({"ok": False, "error": "`title` is required"})
            card = kanban.create_card(
                title=title,
                column=args.get("column", "todo"),
                notes=args.get("notes", ""),
                tags=args.get("tags") or [],
            )
            return json.dumps({"ok": True, "card": card.to_dict()})

        if action == "move":
            card_id = args.get("id", "")
            column = args.get("column", "")
            if not card_id or not column:
                return json.dumps({"ok": False, "error": "`id` and `column` are required"})
            card = kanban.move_card(card_id, column)
            return json.dumps({"ok": True, "card": card.to_dict()})

        if action == "update":
            card_id = args.get("id", "")
            if not card_id:
                return json.dumps({"ok": False, "error": "`id` is required"})
            updates: dict[str, Any] = {}
            for key in ("title", "notes", "tags", "column"):
                if key in args:
                    updates[key] = args[key]
            card = kanban.update_card(card_id, updates)
            return json.dumps({"ok": True, "card": card.to_dict()})

        if action == "delete":
            card_id = args.get("id", "")
            if not card_id:
                return json.dumps({"ok": False, "error": "`id` is required"})
            kanban.delete_card(card_id)
            return json.dumps({"ok": True})

        return json.dumps({"ok": False, "error": f"unknown action: {action!r}"})

    except (KeyError, ValueError, OSError) as exc:
        return json.dumps({"ok": False, "error": str(exc)})
