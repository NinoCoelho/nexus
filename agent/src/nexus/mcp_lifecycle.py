"""MCP lifecycle management for the Nexus server.

Creates a :class:`loom.mcp.McpManager` from the ``[mcp]`` config section,
connects to all configured servers during lifespan startup, discovers tools,
and registers them into the agent's live ``ToolRegistry``.

The manager is stored on ``app.state.mcp_manager`` and closed during shutdown.
"""

from __future__ import annotations

import logging
from typing import Any

from loom.mcp.client import McpClient
from loom.mcp.config import McpServerConfig
from loom.mcp.handler import McpToolHandler
from loom.mcp.manager import McpManager

from .secrets_substitute import resolve as _resolve_secrets

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
            env=_resolve_secrets(entry.env),
            url=_resolve_secrets(entry.url) or None,
            headers=_resolve_secrets(entry.headers),
        )
        configs.append(cfg)
    if not configs:
        return None
    return McpManager(configs)


async def start_mcp(
    manager: McpManager,
    tool_registry: Any,
    agent: Any | None = None,
) -> list[McpToolHandler]:
    """Connect all MCP servers and register discovered tools.

    If *agent* is provided, wires sampling (LLM) and elicitation (HITL)
    callbacks so MCP servers can request completions and user input.
    """
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

    if agent is not None:
        _wire_sampling(manager, agent)
        _wire_elicitation(manager, agent)

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


async def test_mcp_connection(config: dict[str, Any]) -> dict[str, Any]:
    """Test an MCP server config by connecting, listing tools, disconnecting.

    Accepts a raw dict with transport/command/url/env/headers. Returns
    ok/tool_count/tools/error. Does NOT persist anything.
    """
    transport = config.get("transport", "stdio")
    command = config.get("command") or []
    url = _resolve_secrets(config.get("url") or "")
    env = _resolve_secrets(config.get("env") or {})
    headers = _resolve_secrets(config.get("headers") or {})
    name = config.get("name", "test")

    if transport == "stdio" and isinstance(command, str):
        command = command.split()
    if transport != "stdio" and isinstance(command, list) and not url:
        url = ""
    if transport == "stdio" and not command:
        return {"ok": False, "error": "Command is required for stdio transport", "tool_count": 0, "tools": []}
    if transport != "stdio" and not url:
        return {"ok": False, "error": "URL is required for remote transport", "tool_count": 0, "tools": []}

    cfg = McpServerConfig(
        name=name,
        transport=transport,
        command=command or None,
        env=env,
        url=url or None,
        headers=headers,
    )
    try:
        client = McpClient(cfg)
        async with client:
            handlers = await client.list_tools()
            tools = [h.tool.name for h in handlers]
            return {
                "ok": True,
                "tool_count": len(tools),
                "tools": tools,
                "error": None,
            }
    except Exception as e:
        log.warning("[mcp] test connection failed: %s", e)
        return {"ok": False, "error": str(e), "tool_count": 0, "tools": []}


def start_mcp_server(
    nexus_cfg: Any,
    tool_registry: Any,
) -> Any | None:
    """Optionally start an MCP server exposing Nexus tools to external hosts.

    Returns the bridge instance if started, None otherwise.
    The bridge runs in a background thread serving Streamable HTTP.
    """
    mcp_cfg = getattr(nexus_cfg, "mcp", None)
    if mcp_cfg is None or not getattr(mcp_cfg, "server_enabled", False):
        return None
    try:
        from loom.mcp.server_bridge import McpServerBridge
    except ImportError:
        log.warning("[mcp-server] mcp package not installed — skipping MCP server mode")
        return None

    expose = getattr(mcp_cfg, "server_expose", None) or None
    port = getattr(mcp_cfg, "server_port", 18990)

    bridge = McpServerBridge(
        tool_registry,
        name="nexus",
        expose=expose,
    )
    import threading

    def _run() -> None:
        try:
            bridge.run(host="127.0.0.1", port=port)
        except Exception:
            log.exception("[mcp-server] failed")

    thread = threading.Thread(target=_run, daemon=True, name="mcp-server")
    thread.start()
    log.info("[mcp-server] started on 127.0.0.1:%d (expose=%s)", port, expose or "all")
    return bridge


def _wire_sampling(manager: McpManager, agent: Any) -> None:
    """Wire MCP sampling requests to the agent's LLM provider."""
    import json

    async def _sampling(**kwargs: Any) -> str:
        messages = kwargs.get("messages", [])
        max_tokens = kwargs.get("max_tokens", 256)
        system_prompt = kwargs.get("system_prompt", "")
        from loom.types import ChatMessage, Role

        loom_messages: list[ChatMessage] = []
        if system_prompt:
            loom_messages.append(ChatMessage(role=Role.SYSTEM, content=system_prompt))
        for msg in messages:
            role = Role.USER if msg.get("role") == "user" else Role.ASSISTANT
            content = msg.get("content", "")
            if isinstance(content, dict):
                content = content.get("text", json.dumps(content))
            loom_messages.append(ChatMessage(role=role, content=content))
        try:
            provider = agent._nexus_provider
            result = await provider.chat(loom_messages, max_tokens=max_tokens)
            return result
        except Exception as e:
            log.exception("[mcp] sampling failed")
            return f"[sampling error: {e}]"

    manager.sampling_fn = _sampling


def _wire_elicitation(manager: McpManager, agent: Any) -> None:
    """Wire MCP elicitation requests to the agent's HITL system."""
    import json

    async def _elicitation(message: str, schema: dict) -> dict | None:
        ask_user = getattr(agent._handlers, "ask_user", None)
        if ask_user is None:
            log.warning("[mcp] elicitation requested but no ask_user handler wired")
            return None
        fields_desc = ""
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        for fname, fdef in props.items():
            req = " (required)" if fname in required else ""
            desc = fdef.get("description", "")
            fields_desc += f"\n- {fname}: {desc}{req}"
        prompt = (
            f"MCP server is requesting input:\n\n"
            f"{message}\n\n"
            f"Fields:{fields_desc}\n\n"
            f"Respond with a JSON object containing the requested fields."
        )
        try:
            result = await ask_user.invoke({
                "kind": "text",
                "message": prompt,
            })
            answer = getattr(result, "answer", None) or ""
            if answer.startswith("{"):
                return json.loads(answer)
            return {"response": answer}
        except Exception:
            log.exception("[mcp] elicitation failed")
            return None

    manager.elicitation_fn = _elicitation
