"""MCP server management API routes.

Provides read-only status and reconnect/refresh endpoints for connected
MCP servers.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

log = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp"])


def _get_manager(request: Request) -> Any:
    mgr = getattr(request.app.state, "mcp_manager", None)
    if mgr is None:
        raise HTTPException(status_code=503, detail="MCP not configured")
    return mgr


@router.get("/servers")
async def list_servers(request: Request) -> list[dict[str, Any]]:
    mgr = _get_manager(request)
    out: list[dict[str, Any]] = []
    for name in mgr.server_names:
        connected = mgr.is_connected(name)
        entry: dict[str, Any] = {
            "name": name,
            "connected": connected,
        }
        if connected:
            cached = getattr(mgr, "_cached_handlers", [])
            tools = [h for h in cached if h.namespace == name or (name and h.tool.name.startswith(f"{name}__"))]
            entry["tool_count"] = len(tools)
            entry["tools"] = [h.tool.name for h in tools]
        out.append(entry)
    return out


@router.post("/servers/{server_name}/reconnect")
async def reconnect_server(server_name: str, request: Request) -> dict[str, Any]:
    mgr = _get_manager(request)
    try:
        await mgr.reconnect(server_name)
        tool_reg = getattr(request.app.state.agent._loom, "_tools", None)
        if tool_reg is not None:
            from ..mcp_lifecycle import refresh_mcp_tools
            await refresh_mcp_tools(mgr, tool_reg)
        return {"ok": True, "server": server_name, "connected": True}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        log.exception("[mcp] reconnect failed for %r", server_name)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/refresh")
async def refresh_all_tools(request: Request) -> dict[str, Any]:
    mgr = _get_manager(request)
    tool_reg = getattr(request.app.state.agent._loom, "_tools", None)
    if tool_reg is None:
        raise HTTPException(status_code=503, detail="tool registry not available")
    from ..mcp_lifecycle import refresh_mcp_tools
    handlers = await refresh_mcp_tools(mgr, tool_reg)
    return {"ok": True, "tool_count": len(handlers), "servers": mgr.connected_servers}
