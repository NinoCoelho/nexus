"""MCP lifecycle management for the Nexus server.

Creates a :class:`loom.mcp.McpManager` from the ``[mcp]`` config section,
connects to all configured servers during lifespan startup, discovers tools,
and registers them into the agent's live ``ToolRegistry``.

The manager is stored on ``app.state.mcp_manager`` and closed during shutdown.
"""

from __future__ import annotations

import logging
from typing import Any

from loom.mcp.config import McpServerConfig
from loom.mcp.handler import McpToolHandler
from loom.mcp.manager import McpManager

log = logging.getLogger(__name__)


def build_mcp_manager(nexus_cfg: Any) -> McpManager | None:
    """Build an McpManager from the Nexus config, or return None if no servers."""
    mcp_cfg = getattr(nexus_cfg, "mcp", None)
    if mcp_cfg is None:
        return None
    servers = getattr(mcp_cfg, "servers", {})
    if not servers:
        return None
    configs: list[McpServerConfig] = []
    for name, entry in servers.items():
        if not getattr(entry, "enabled", True):
            continue
        cfg = McpServerConfig(
            name=name,
            transport=entry.transport,
            command=entry.command or None,
            env=entry.env,
            url=entry.url or None,
            headers=entry.headers,
        )
        configs.append(cfg)
    if not configs:
        return None
    return McpManager(configs)


async def start_mcp(
    manager: McpManager,
    tool_registry: Any,
) -> list[McpToolHandler]:
    """Connect all MCP servers and register discovered tools."""
    await manager.__aenter__()
    handlers = await manager.all_tool_handlers()
    for handler in handlers:
        try:
            tool_registry.register(handler)
            log.debug("[mcp] registered tool %s", handler.tool.name)
        except Exception:
            log.exception("[mcp] failed to register tool %s", handler.tool.name)
    log.info(
        "[mcp] connected to %d server(s), registered %d tool(s)",
        len(manager.connected_servers),
        len(handlers),
    )
    manager._cached_handlers = handlers
    return handlers


async def refresh_mcp_tools(
    manager: McpManager,
    tool_registry: Any,
) -> list[McpToolHandler]:
    """Re-discover tools from all servers and update the registry."""
    # Remove old MCP tools
    old = getattr(manager, "_cached_handlers", [])
    for handler in old:
        name = handler.tool.name
        try:
            tool_registry.unregister(name)
        except Exception:
            pass
    # Discover new tools
    handlers = await manager.all_tool_handlers()
    for handler in handlers:
        try:
            tool_registry.register(handler)
        except Exception:
            log.exception("[mcp] failed to register tool %s", handler.tool.name)
    manager._cached_handlers = handlers
    log.info("[mcp] refreshed: %d tools from %d servers", len(handlers), len(manager.connected_servers))
    return handlers


async def stop_mcp(manager: McpManager) -> None:
    """Gracefully close all MCP server connections."""
    await manager.__aexit__(None, None, None)
    log.info("[mcp] all connections closed")
