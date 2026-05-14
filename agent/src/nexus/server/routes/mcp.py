"""MCP server management API routes.

Provides read-only status, reconnect/refresh endpoints for connected
MCP servers, and access to MCP resources and prompt templates.
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


@router.get("/resources")
async def list_resources(request: Request) -> list[dict[str, Any]]:
    mgr = _get_manager(request)
    try:
        return await mgr.all_resource_specs()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/resources/{server_name}")
async def read_resource(server_name: str, uri: str, request: Request) -> dict[str, Any]:
    mgr = _get_manager(request)
    try:
        content = await mgr.read_resource(server_name, uri)
        return {"ok": True, "content": content, "server": server_name, "uri": uri}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/prompts")
async def list_prompts(request: Request) -> list[dict[str, Any]]:
    mgr = _get_manager(request)
    try:
        return await mgr.all_prompt_specs()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/prompts/{server_name}/{prompt_name}")
async def render_prompt(
    server_name: str,
    prompt_name: str,
    request: Request,
) -> dict[str, Any]:
    mgr = _get_manager(request)
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    args = body.get("arguments") if isinstance(body, dict) else None
    try:
        content = await mgr.get_prompt(server_name, prompt_name, args)
        return {"ok": True, "content": content, "server": server_name, "prompt": prompt_name}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/app/{server_name}")
async def fetch_app_resource(server_name: str, uri: str, request: Request) -> dict[str, Any]:
    """Fetch an MCP App HTML resource from a connected server."""
    mgr = _get_manager(request)
    try:
        content = await mgr.read_resource(server_name, uri)
        return {"ok": True, "html": content, "server": server_name, "uri": uri}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/test")
async def test_mcp_connection(request: Request) -> dict[str, Any]:
    """Test a single MCP server config by connecting, listing tools, then disconnecting.

    Accepts a JSON body with the server configuration. Does NOT persist anything.
    """
    body = await request.json()
    from ..mcp_lifecycle import test_mcp_connection
    try:
        return await test_mcp_connection(body)
    except Exception as e:
        return {"ok": False, "error": str(e), "tool_count": 0, "tools": []}
