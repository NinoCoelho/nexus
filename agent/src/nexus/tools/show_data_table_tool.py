"""show_data_table agent tool — renders a data table as an inline MCP App."""

from __future__ import annotations

import json

from ..agent.llm import ToolSpec

SHOW_DATA_TABLE_TOOL = ToolSpec(
    name="show_data_table",
    description=(
        "Render a data table as an interactive table view in the chat. "
        "Use this to present tabular data to the user in a visual format "
        "after reading or querying a data-table file."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Vault-relative path to the data-table .md file.",
            },
        },
        "required": ["path"],
    },
    meta={"ui": {"resourceUri": "ui://nexus/data-table?path={path}"}},
)


async def handle_show_data_table(args: dict) -> str:
    path = args.get("path", "")
    if not path:
        return json.dumps({"ok": False, "error": "path is required"})
    return json.dumps({"ok": True, "resourceUri": f"ui://nexus/data-table?path={path}"})
