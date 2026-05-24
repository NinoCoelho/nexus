"""show_kanban agent tool — renders a kanban board as an inline MCP App."""

from __future__ import annotations

import json

from ..agent.llm import ToolSpec

SHOW_KANBAN_TOOL = ToolSpec(
    name="show_kanban",
    description=(
        "Render a kanban board as an interactive visual card in the chat. "
        "Use this after viewing or modifying a kanban board to give the user "
        "a visual summary. The board is rendered inline with lanes and cards."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Vault-relative path to the kanban .md file (e.g. 'boards/work.md').",
            },
        },
        "required": ["path"],
    },
    meta={"ui": {"resourceUri": "ui://nexus/kanban?path={path}"}},
)


async def handle_show_kanban(args: dict) -> str:
    path = args.get("path", "")
    if not path:
        return json.dumps({"ok": False, "error": "path is required"})
    return json.dumps({"ok": True, "resourceUri": f"ui://nexus/kanban?path={path}"})
