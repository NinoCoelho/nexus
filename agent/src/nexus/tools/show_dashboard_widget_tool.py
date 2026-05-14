"""show_dashboard_widget agent tool — renders a dashboard chart as an inline MCP App."""

from __future__ import annotations

import json

from ..agent.llm import ToolSpec

SHOW_DASHBOARD_WIDGET_TOOL = ToolSpec(
    name="show_dashboard_widget",
    description=(
        "Render a dashboard widget as an interactive chart in the chat. "
        "Use this to visualize a specific chart widget from a database dashboard. "
        "Supports bar, line, area, pie, donut, and KPI chart types."
    ),
    parameters={
        "type": "object",
        "properties": {
            "folder": {
                "type": "string",
                "description": "Vault-relative folder path of the database (e.g. 'crm').",
            },
            "widget_id": {
                "type": "string",
                "description": "The id of the dashboard widget to render.",
            },
        },
        "required": ["folder", "widget_id"],
    },
    meta={"ui": {"resourceUri": "ui://nexus/dashboard-widget?folder={folder}&widget_id={widget_id}"}},
)


async def handle_show_dashboard_widget(args: dict) -> str:
    folder = args.get("folder", "")
    widget_id = args.get("widget_id", "")
    if not folder or not widget_id:
        return json.dumps({"ok": False, "error": "folder and widget_id are required"})
    return json.dumps({
        "ok": True,
        "resourceUri": f"ui://nexus/dashboard-widget?folder={folder}&widget_id={widget_id}",
    })
