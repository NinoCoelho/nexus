"""Kanban agent tool: kanban_manage.

Operates on kanban boards stored as Obsidian-compatible markdown files in the
vault. The agent addresses boards by their vault-relative path
(e.g. "boards/projects.md"). If the file doesn't exist yet, use
action="create_board" to scaffold one.
"""

from __future__ import annotations

import json
from typing import Any

from ..agent.llm import ToolSpec

KANBAN_MANAGE_TOOL = ToolSpec(
    name="kanban_manage",
    description=(
        "Manage kanban boards stored as markdown in the vault. "
        "Each board is a single .md file with `kanban-plugin: basic` frontmatter. "
        "Actions: create_board, view, add_card, move_card, update_card, delete_card, "
        "add_lane, delete_lane."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "create_board", "view",
                    "add_card", "move_card", "update_card", "delete_card",
                    "add_lane", "delete_lane",
                ],
                "description": "Action to perform.",
            },
            "path": {
                "type": "string",
                "description": "Vault-relative path to the kanban .md file (e.g. 'boards/work.md').",
            },
            "title": {
                "type": "string",
                "description": "Board title (create_board), card title (add_card/update_card), or lane title (add_lane).",
            },
            "columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Initial lane titles for create_board (default: ['Todo','Doing','Done']).",
            },
            "lane": {"type": "string", "description": "Lane id (move_card, add_card, delete_lane)."},
            "card_id": {"type": "string", "description": "Card id (move/update/delete)."},
            "body": {"type": "string", "description": "Card body / notes markdown."},
            "position": {"type": "integer", "description": "Insert position within the lane (move_card)."},
        },
        "required": ["action", "path"],
    },
)


def handle_kanban_tool(args: dict[str, Any]) -> str:
    from .. import vault_kanban

    action = args.get("action", "")
    path = args.get("path", "")
    if not path:
        return json.dumps({"ok": False, "error": "`path` is required"})

    try:
        if action == "create_board":
            board = vault_kanban.create_empty(
                path,
                title=args.get("title"),
                columns=args.get("columns"),
            )
            return json.dumps({"ok": True, "path": path, "board": board.to_dict()})

        if action == "view":
            board = vault_kanban.read_board(path)
            return json.dumps({"ok": True, "path": path, "board": board.to_dict()})

        if action == "add_card":
            lane = args.get("lane", "")
            title = args.get("title", "")
            if not lane or not title:
                return json.dumps({"ok": False, "error": "`lane` and `title` are required"})
            card = vault_kanban.add_card(path, lane, title, args.get("body", ""))
            return json.dumps({"ok": True, "card": card.to_dict()})

        if action == "move_card":
            card_id = args.get("card_id", "")
            lane = args.get("lane", "")
            if not card_id or not lane:
                return json.dumps({"ok": False, "error": "`card_id` and `lane` are required"})
            card = vault_kanban.move_card(path, card_id, lane, args.get("position"))
            return json.dumps({"ok": True, "card": card.to_dict()})

        if action == "update_card":
            card_id = args.get("card_id", "")
            if not card_id:
                return json.dumps({"ok": False, "error": "`card_id` is required"})
            updates: dict[str, Any] = {}
            for key in ("title", "body"):
                if key in args:
                    updates[key] = args[key]
            card = vault_kanban.update_card(path, card_id, updates)
            return json.dumps({"ok": True, "card": card.to_dict()})

        if action == "delete_card":
            card_id = args.get("card_id", "")
            if not card_id:
                return json.dumps({"ok": False, "error": "`card_id` is required"})
            vault_kanban.delete_card(path, card_id)
            return json.dumps({"ok": True})

        if action == "add_lane":
            title = args.get("title", "")
            if not title:
                return json.dumps({"ok": False, "error": "`title` is required"})
            lane = vault_kanban.add_lane(path, title)
            return json.dumps({"ok": True, "lane": lane.to_dict()})

        if action == "delete_lane":
            lane_id = args.get("lane", "")
            if not lane_id:
                return json.dumps({"ok": False, "error": "`lane` is required"})
            vault_kanban.delete_lane(path, lane_id)
            return json.dumps({"ok": True})

        return json.dumps({"ok": False, "error": f"unknown action: {action!r}"})

    except (KeyError, ValueError, FileNotFoundError, OSError) as exc:
        return json.dumps({"ok": False, "error": str(exc)})
