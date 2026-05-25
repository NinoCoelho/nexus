from __future__ import annotations

from typing import Any

from ..expressions import resolve_templates
from ..models import StepConfig


async def execute_step(
    engine: Any,
    step: StepConfig,
    ctx: dict[str, Any],
    resolved_input: dict[str, Any] | None,
) -> Any:
    server_name = step.mcp_server
    tool_name = step.mcp_tool
    if not server_name or not tool_name:
        raise ValueError(f"step '{step.name}' missing mcp_server or mcp_tool")

    mgr = getattr(engine, "_mcp_manager", None)
    if mgr is None:
        try:
            from ...server.app import app

            mgr = getattr(app.state, "mcp_manager", None)
        except Exception:
            pass
    if mgr is None:
        raise RuntimeError("MCP manager not available")

    client = mgr._clients.get(server_name)
    if client is None:
        raise ValueError(f"MCP server '{server_name}' not connected")

    resolved_input_mcp = resolve_templates(step.input or {}, ctx)
    result = await client.call_tool(tool_name, resolved_input_mcp)
    return result
