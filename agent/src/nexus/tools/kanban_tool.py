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
        "Actions: create_board, view, update_board, "
        "add_card, move_card, update_card, delete_card, "
        "add_lane, update_lane, move_lane, delete_lane.\n\n"
        "Safe usage pattern:\n"
        "- Before `update_card`/`delete_card`/`update_lane`/`delete_lane`, call `view` "
        "to inspect the current board state and confirm the target exists.\n"
        "- `delete_card` and `delete_lane` are irreversible.\n\n"
        "Automated workflow pattern:\n"
        "1. `create_board` for a project.\n"
        "2. `add_lane` for each stage; use `update_lane` with `prompt` to bind an "
        "agent behavior that auto-dispatches when cards enter the lane.\n"
        "3. `add_card` with a problem description in the body.\n"
        "4. `move_card` into a lane to auto-dispatch the agent with that lane's "
        "prompt + card body as context."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "create_board", "view", "update_board",
                    "add_card", "move_card", "update_card", "delete_card",
                    "add_lane", "update_lane", "move_lane", "delete_lane",
                ],
                "description": "Action to perform.",
            },
            "path": {
                "type": "string",
                "description": "Vault-relative path to the kanban .md file (e.g. 'boards/work.md').",
            },
            "title": {
                "type": "string",
                "description": "Board title (create_board), card title (add_card/update_card), or lane title (add_lane/update_lane).",
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Auto-dispatch prompt for update_lane. When a card is added/moved into "
                    "the lane, the agent is auto-dispatched with this prompt as context. "
                    "Empty string clears."
                ),
            },
            "board_prompt": {
                "type": "string",
                "description": (
                    "Board-level system prompt for update_board. Prepended before any "
                    "lane prompt on every dispatch — use it to set personality, behaviour, "
                    "and general instructions for all cards on this board. Empty string clears."
                ),
            },
            "model": {
                "type": "string",
                "description": "Model id used for this lane's auto-dispatched runs (update_lane). Empty string clears.",
            },
            "columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Initial lane titles for create_board (default: ['Todo','Doing','Done']).",
            },
            "lane": {"type": "string", "description": "Lane id (move_card, add_card, delete_lane)."},
            "card_id": {"type": "string", "description": "Card id (move/update/delete)."},
            "body": {
                "type": "string",
                "description": (
                    "Card body in markdown. Headings, sub-lists, fenced code blocks "
                    "and blockquotes are all supported and preserved verbatim — write "
                    "rich markdown freely without escaping."
                ),
            },
            "position": {
                "type": "integer",
                "description": (
                    "Insert position within the destination lane (move_card), "
                    "or the lane's new column index within the board (move_lane)."
                ),
            },
            "due": {"type": "string", "description": "ISO date 'YYYY-MM-DD' for update_card. Empty string clears."},
            "priority": {
                "type": "string",
                "enum": ["", "low", "med", "high", "urgent"],
                "description": "Card priority (update_card). Empty string clears.",
            },
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Replace card labels (update_card). Pass [] to clear.",
            },
            "assignees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Replace card assignees (update_card). Pass [] to clear.",
            },
            "status": {
                "type": "string",
                "enum": ["", "running", "done", "failed"],
                "description": "Card run status (update_card). Empty string clears.",
            },
        },
        "required": ["action", "path"],
    },
)


def handle_kanban_tool(args: dict[str, Any]) -> str:
    """Dispatch the requested kanban action and return serialized JSON.

    Args:
        args: Dict containing ``action``, ``path``, and optional fields depending
              on the action (e.g. ``card_id``, ``lane``, ``title``, ``body``).

    Returns:
        JSON with ``{"ok": true, ...}`` on success or ``{"ok": false, "error": ...}``
        for invalid arguments, missing files, or I/O errors.
    """
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

        if action == "update_board":
            updates: dict[str, Any] = {}
            if "title" in args:
                updates["title"] = args["title"]
            if "board_prompt" in args:
                bp = args["board_prompt"]
                updates["board_prompt"] = bp if bp else None
            if not updates:
                return json.dumps({"ok": False, "error": "no fields to update (title/board_prompt)"})
            board = vault_kanban.update_board(path, updates)
            return json.dumps({"ok": True, "board": board.to_dict()})

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
            for key in ("title", "body", "due", "priority", "labels", "assignees", "status"):
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

        if action == "update_lane":
            lane_id = args.get("lane", "")
            if not lane_id:
                return json.dumps({"ok": False, "error": "`lane` (id) is required"})
            updates: dict[str, Any] = {}
            for key in ("title", "prompt", "model"):
                if key in args:
                    updates[key] = args[key]
            if not updates:
                return json.dumps({"ok": False, "error": "no fields to update (title/prompt/model)"})
            lane = vault_kanban.update_lane(path, lane_id, updates)
            return json.dumps({"ok": True, "lane": lane.to_dict()})

        if action == "move_lane":
            lane_id = args.get("lane", "")
            if not lane_id:
                return json.dumps({"ok": False, "error": "`lane` (id) is required"})
            lane = vault_kanban.move_lane(path, lane_id, args.get("position"))
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
