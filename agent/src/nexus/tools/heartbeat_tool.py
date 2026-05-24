"""manage_heartbeat agent tool.

Lets the agent create, list, enable, disable, and delete heartbeats.
The manager is resolved lazily via a getter because the heartbeat registry
and store are created inside the lifespan (after the tool registry is built).
"""

from __future__ import annotations

import json
from typing import Any, Callable

from ..agent.llm import ToolSpec

HEARTBEAT_MANAGE_TOOL = ToolSpec(
    name="manage_heartbeat",
    description=(
        "Create, delete, enable, disable, or list heartbeats. "
        "A heartbeat is a recurring scheduled task: the driver detects events "
        "and the agent processes them. "
        "Actions: create | delete | enable | disable | list."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "delete", "enable", "disable", "list"],
                "description": "Operation to perform.",
            },
            "name": {
                "type": "string",
                "description": (
                    "Heartbeat identifier (slug: [a-zA-Z0-9_-], max 64 chars). "
                    "Must match the directory name."
                ),
            },
            "description": {
                "type": "string",
                "description": "One-line description of what this heartbeat does.",
            },
            "schedule": {
                "type": "string",
                "description": (
                    "When to run. Accepts cron (e.g. '*/5 * * * *'), "
                    "@daily / @hourly shorthands, or natural language "
                    "('every 5 minutes', 'every hour', 'every 2 days')."
                ),
            },
            "instructions": {
                "type": "string",
                "description": "Markdown instructions for the agent when an event fires.",
            },
            "driver_code": {
                "type": "string",
                "description": (
                    "Python source for driver.py. Must define a class named "
                    "'Driver' that subclasses HeartbeatDriver and implements "
                    "async check(self, state: dict) -> tuple[list[HeartbeatEvent], dict]."
                ),
            },
        },
        "required": ["action"],
    },
)


async def handle_heartbeat_manage_tool(
    args: dict[str, Any],
    manager_getter: Callable[[], Any] | None,
) -> str:
    if manager_getter is None:
        return json.dumps({
            "ok": False,
            "error": "manage_heartbeat unavailable: heartbeat system not initialised",
        })
    try:
        manager = manager_getter()
        result = manager.invoke(args)
        is_error = result.startswith("error:")
        return json.dumps({"ok": not is_error, "message": result})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})
