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
            try:
                from ...mcp_lifecycle import refresh_mcp_tools
                await refresh_mcp_tools(mgr, tool_reg)
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e)) from e
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
    from ...mcp_lifecycle import refresh_mcp_tools
    try:
        handlers = await refresh_mcp_tools(mgr, tool_reg)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"ok": True, "tool_count": len(handlers), "servers": mgr.connected_servers}


@router.post("/reload")
async def reload_mcp(request: Request) -> dict[str, Any]:
    """Hot-reload MCP servers from the current config (no restart needed).

    Stops the old manager, re-reads config, builds a new manager,
    connects all servers, and registers tools into the live registry.
    """
    agent = getattr(request.app.state, "agent", None)
    tool_reg = getattr(agent._loom, "_tools", None) if agent else None
    if tool_reg is None:
        raise HTTPException(status_code=503, detail="agent or tool registry not available")

    from ...config_file import load as load_config
    from ...mcp_lifecycle import build_mcp_manager, start_mcp, stop_mcp

    try:
        cfg = load_config()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"config load failed: {e}") from e

    old_mgr = getattr(request.app.state, "mcp_manager", None)

    new_mgr = build_mcp_manager(cfg)
    if new_mgr is None:
        if old_mgr is not None:
            try:
                old_handlers = getattr(old_mgr, "_cached_handlers", [])
                for h in old_handlers:
                    try:
                        tool_reg.unregister(h.tool.name)
                    except Exception:
                        pass
                await stop_mcp(old_mgr)
            except Exception:
                log.exception("[mcp] failed to stop old manager during reload")
            request.app.state.mcp_manager = None
        return {"ok": True, "tool_count": 0, "servers": [], "message": "no servers configured"}

    if old_mgr is not None:
        old_handlers = getattr(old_mgr, "_cached_handlers", [])
        for h in old_handlers:
            try:
                tool_reg.unregister(h.tool.name)
            except Exception:
                pass
        try:
            await stop_mcp(old_mgr)
        except Exception:
            log.exception("[mcp] failed to stop old manager during reload")

    try:
        agent_obj = agent if agent else None
        handlers = await start_mcp(new_mgr, tool_reg, agent=agent_obj)
        request.app.state.mcp_manager = new_mgr
        log.info("[mcp] reload: %d servers, %d tools", len(new_mgr.connected_servers), len(handlers))
        return {
            "ok": True,
            "tool_count": len(handlers),
            "servers": new_mgr.connected_servers,
        }
    except Exception as e:
        log.exception("[mcp] reload failed during connect")
        request.app.state.mcp_manager = None
        raise HTTPException(status_code=500, detail=str(e)) from e


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


@router.get("/tools")
async def list_tools(request: Request) -> list[dict[str, Any]]:
    """Return all registered MCP tools + internal nexus tools with their meta (including resourceUri)."""
    out: list[dict[str, Any]] = []

    # MCP server tools
    mgr = getattr(request.app.state, "mcp_manager", None)
    if mgr is not None:
        cached = getattr(mgr, "_cached_handlers", [])
        for h in cached:
            entry: dict[str, Any] = {
                "name": h.tool.name,
                "description": h.tool.description,
            }
            if h.meta:
                entry["meta"] = h.meta
            out.append(entry)

    # Internal nexus tools with meta
    agent = getattr(request.app.state, "agent", None)
    tool_reg = getattr(agent._loom, "_tools", None) if agent else None
    if tool_reg is not None:
        for name in tool_reg._handlers:
            handler = tool_reg._handlers[name]
            spec = handler.tool if hasattr(handler, "tool") else None
            if spec is None:
                continue
            meta = getattr(spec, "meta", None)
            if not meta:
                continue
            entry = {
                "name": spec.name,
                "description": spec.description,
                "meta": meta,
                "server_name": "nexus",
            }
            out.append(entry)

    return out


@router.post("/call-tool")
async def call_tool(request: Request) -> dict[str, Any]:
    """Call a tool on a connected MCP server or internal nexus tool directly (for iframe callbacks)."""
    body = await request.json()
    server_name = body.get("server_name", "")
    tool_name = body.get("tool_name", "")
    arguments = body.get("arguments") or {}
    if not tool_name:
        raise HTTPException(status_code=400, detail="tool_name is required")

    # Internal nexus tools
    if server_name == "nexus":
        agent = getattr(request.app.state, "agent", None)
        tool_reg = getattr(agent._loom, "_tools", None) if agent else None
        if tool_reg is None:
            raise HTTPException(status_code=503, detail="agent or tool registry not available")
        try:
            result = await tool_reg.dispatch(tool_name, arguments)
            return {
                "ok": True,
                "text": result.to_text(),
                "is_error": result.is_error,
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    # MCP server tools
    if not server_name:
        raise HTTPException(status_code=400, detail="server_name is required")
    mgr = _get_manager(request)
    try:
        client = mgr._clients.get(server_name)
        if client is None:
            raise HTTPException(status_code=404, detail=f"Server {server_name!r} not connected")
        result = await client.call_tool(tool_name, arguments)
        return {
            "ok": True,
            "text": result.to_text(),
            "is_error": result.is_error,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/internal-resource")
async def fetch_internal_resource(uri: str) -> dict[str, Any]:
    """Resolve a ``ui://nexus/*`` URI and return self-contained HTML."""
    if not uri or not uri.startswith("ui://nexus/"):
        raise HTTPException(status_code=400, detail="uri must start with ui://nexus/")
    from ...mcp_resources import resolve

    try:
        html_content = resolve(uri)
        return {"ok": True, "html": html_content, "uri": uri}
    except Exception as e:
        log.exception("internal resource failed for %r", uri)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/test")
async def test_mcp_connection(request: Request) -> dict[str, Any]:
    """Test a single MCP server config by connecting, listing tools, then disconnecting.

    Accepts a JSON body with the server configuration. Does NOT persist anything.
    """
    body = await request.json()
    try:
        from ...mcp_lifecycle import test_mcp_connection as _test
        return await _test(body)
    except Exception as e:
        return {"ok": False, "error": str(e), "tool_count": 0, "tools": []}
