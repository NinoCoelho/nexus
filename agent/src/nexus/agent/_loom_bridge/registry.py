"""Tool registry builder — registers Nexus tool handlers into a loom ToolRegistry."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from loom.tools.base import ToolHandler, ToolResult
from loom.tools.registry import ToolRegistry

from nexus.agent.llm import ToolSpec

if TYPE_CHECKING:
    from loom.home import AgentHome
    from loom.permissions import AgentPermissions


class _SimpleToolHandler(ToolHandler):
    """Adapts a sync or async callable(args: dict) -> str into loom ToolHandler."""

    def __init__(self, spec: ToolSpec, fn: Any) -> None:
        self._spec = spec
        self._fn = fn

    @property
    def tool(self) -> ToolSpec:
        return self._spec

    async def invoke(self, args: dict) -> ToolResult:
        import inspect
        result = self._fn(args)
        if inspect.isawaitable(result):
            result = await result
        text = result if isinstance(result, str) else json.dumps(result)
        return ToolResult(text=text)


class AgentHandlers:
    """Mutable holder for late-bound HITL handlers.

    Built once and shared between the tool registry closures and the
    Agent façade.  When ``app.py`` sets ``agent._ask_user_handler``,
    the corresponding attribute here is updated so all registry
    closures see the new value without a registry rebuild.
    """

    def __init__(
        self,
        ask_user: Any | None = None,
        terminal: Any | None = None,
        dispatcher: Any | None = None,
    ) -> None:
        self.ask_user = ask_user
        self.terminal = terminal
        # Async callable: dispatcher(path, card_id?, mode) -> dict
        # with keys {session_id, seed_message?, path, card_id?, mode}.
        # Late-bound by app.py so tools can spawn sub-sessions.
        self.dispatcher = dispatcher


def build_tool_registry(
    *,
    skill_registry: Any,
    handlers: AgentHandlers,
    search_cfg: Any | None = None,
    scrape_cfg: Any | None = None,
    home: "AgentHome | None" = None,
    permissions: "AgentPermissions | None" = None,
) -> ToolRegistry:
    """Build a loom ToolRegistry populated with all Nexus tools.

    HITL handler closures read from ``handlers`` at dispatch time, so
    late-binding by the server (setting ``handlers.ask_user`` after
    registry construction) takes effect on the next tool call.
    """
    from nexus.agent.loop import SKILL_MANAGE_TOOL
    from nexus.skills.manager import SkillManager
    from nexus.tools.acp_call import ACP_CALL_TOOL, acp_call
    from nexus.tools.http_call import HTTP_CALL_TOOL, HttpCallHandler
    from nexus.tools.csv_tool import CSV_TOOL, handle_csv_tool
    from nexus.tools.datatable_tool import DATATABLE_MANAGE_TOOL, handle_datatable_tool
    from nexus.tools.kanban_tool import KANBAN_MANAGE_TOOL, handle_kanban_tool
    from nexus.tools.kanban_query_tool import KANBAN_QUERY_TOOL, handle_kanban_query_tool
    from nexus.tools.calendar_tool import CALENDAR_MANAGE_TOOL, handle_calendar_tool
    from nexus.tools.dispatch_card_tool import DISPATCH_CARD_TOOL, handle_dispatch_card_tool
    from nexus.tools.memory_tool import MEMORY_READ_TOOL, MEMORY_WRITE_TOOL, MemoryHandler
    from nexus.tools.visualize_tool import VISUALIZE_TABLE_TOOL, handle_visualize_tool
    from nexus.tools.state_tool import STATE_TOOLS, StateToolHandler
    from nexus.tools.vault_tool import VAULT_TOOLS, VAULT_SEMANTIC_SEARCH_TOOL, handle_vault_tool
    from nexus.tools.ontology_tool import ONTOLOGY_MANAGE_TOOL, make_ontology_handler
    from nexus.agent.ask_user_tool import ASK_USER_TOOL
    from nexus.agent.terminal_tool import TERMINAL_TOOL

    registry = ToolRegistry()
    state = StateToolHandler(skill_registry)
    manager = SkillManager(skill_registry)
    http = HttpCallHandler()

    # skills_list / skill_view
    for spec in STATE_TOOLS:
        _spec = spec

        async def _state_invoke(args: dict, *, _spec=_spec) -> str:
            return state.invoke(_spec.name, args).to_text()

        registry.register(_SimpleToolHandler(_spec, _state_invoke))

    # skill_manage
    async def _skill_manage(args: dict) -> str:
        action = args.get("action", "")
        result = manager.invoke(action, args)
        return (
            f'{{"ok": {str(result.ok).lower()}, '
            f'"message": {result.message!r}, '
            f'"rolled_back": {str(result.rolled_back).lower()}}}'
        )

    registry.register(_SimpleToolHandler(SKILL_MANAGE_TOOL, _skill_manage))

    # ontology_manage — vault-backed CRUD over GraphRAG ontology + propose flow.
    # ask_user is wired through `handlers` (late-bound) so the propose action
    # can confirm with the user without a registry rebuild. cfg is reloaded
    # per-call so writes that mutate ontology pick up the freshest config
    # before re-initializing the engine.
    async def _ontology_ask_user(args: dict) -> Any:
        h = handlers.ask_user
        if h is None:
            return None
        return await h.invoke(args)

    def _load_cfg() -> Any:
        from nexus.config_file import load as load_config
        return load_config()

    _ontology_handler = make_ontology_handler(
        ask_user=_ontology_ask_user,
        cfg_loader=_load_cfg,
    )

    async def _ontology_manage(args: dict) -> str:
        return await _ontology_handler(args)

    registry.register(_SimpleToolHandler(ONTOLOGY_MANAGE_TOOL, _ontology_manage))

    # http_call
    async def _http_call(args: dict) -> str:
        res = await http.invoke(args)
        return res.to_text()

    registry.register(_SimpleToolHandler(HTTP_CALL_TOOL, _http_call))

    # acp_call — only advertise when an ACP gateway is actually configured.
    # When env vars are missing, hiding the tool keeps it out of the system
    # prompt so the agent doesn't try to call something that will only ever
    # answer "not configured".
    from nexus.tools.acp_call import acp_is_configured

    if acp_is_configured():
        async def _acp_call(args: dict) -> str:
            return await acp_call(args.get("agent_id", ""), args.get("message", ""))

        registry.register(_SimpleToolHandler(ACP_CALL_TOOL, _acp_call))

    # vault tools
    for spec in VAULT_TOOLS:
        _spec = spec

        async def _vault(args: dict, *, _spec=_spec) -> str:
            return handle_vault_tool(_spec.name, args)

        registry.register(_SimpleToolHandler(_spec, _vault))

    # vault_semantic_search (async handler)
    async def _vault_semantic_search(args: dict) -> str:
        return await handle_vault_tool("vault_semantic_search", args)

    registry.register(_SimpleToolHandler(VAULT_SEMANTIC_SEARCH_TOOL, _vault_semantic_search))

    # kanban_manage
    async def _kanban(args: dict) -> str:
        return handle_kanban_tool(args)

    registry.register(_SimpleToolHandler(KANBAN_MANAGE_TOOL, _kanban))

    # kanban_query — cross-board search
    async def _kanban_query(args: dict) -> str:
        return handle_kanban_query_tool(args)

    registry.register(_SimpleToolHandler(KANBAN_QUERY_TOOL, _kanban_query))

    # calendar_manage
    async def _calendar(args: dict) -> str:
        return handle_calendar_tool(args)

    registry.register(_SimpleToolHandler(CALENDAR_MANAGE_TOOL, _calendar))

    # dispatch_card — spawn a chat session seeded from a card or vault file
    async def _dispatch_card(args: dict) -> str:
        return await handle_dispatch_card_tool(args, handlers.dispatcher)

    registry.register(_SimpleToolHandler(DISPATCH_CARD_TOOL, _dispatch_card))

    # datatable_manage
    async def _datatable(args: dict) -> str:
        return handle_datatable_tool(args)

    registry.register(_SimpleToolHandler(DATATABLE_MANAGE_TOOL, _datatable))

    # vault_csv — DuckDB analytics over CSV/TSV files
    async def _csv(args: dict) -> str:
        return handle_csv_tool(args)

    registry.register(_SimpleToolHandler(CSV_TOOL, _csv))

    # visualize_table
    async def _visualize(args: dict) -> str:
        return handle_visualize_tool(args)

    registry.register(_SimpleToolHandler(VISUALIZE_TABLE_TOOL, _visualize))

    _mem_handler = MemoryHandler()

    async def _mem_read(args: dict) -> str:
        return await _mem_handler.read(args.get("key", ""))

    async def _mem_write(args: dict) -> str:
        return await _mem_handler.write(
            args.get("key", ""),
            args.get("content", ""),
            tags=args.get("tags"),
        )

    registry.register(_SimpleToolHandler(MEMORY_READ_TOOL, _mem_read))
    registry.register(_SimpleToolHandler(MEMORY_WRITE_TOOL, _mem_write))

    # HITL tools — always registered; handlers resolved at dispatch time.
    # This lets app.py late-bind handlers without rebuilding the registry.
    async def _ask_user(args: dict) -> str:
        h = handlers.ask_user
        if h is None:
            return '{"ok": false, "error": "ask_user unavailable: handler not wired"}'
        result = await h.invoke(args)
        return result.to_text()

    async def _terminal(args: dict) -> str:
        h = handlers.terminal
        if h is None:
            return '{"ok": false, "error": "terminal unavailable: handler not wired"}'
        result = await h.invoke(args)
        return result.to_text()

    registry.register(_SimpleToolHandler(ASK_USER_TOOL, _ask_user))
    registry.register(_SimpleToolHandler(TERMINAL_TOOL, _terminal))

    # edit_profile — gated by AgentPermissions. Default Loom permissions allow
    # USER.md updates only; SOUL/IDENTITY return permission_denied.
    if home is not None and permissions is not None:
        from loom.tools.profile import EditIdentityTool

        registry.register(EditIdentityTool(home, permissions))

    # Web search — enabled by default via search.enabled (default: True).
    if search_cfg and getattr(search_cfg, "enabled", False):
        import os

        from loom.search import (
            BraveSearchProvider,
            DuckDuckGoSearchProvider,
            SearchStrategy,
            TavilySearchProvider,
        )
        from loom.tools.search import WebSearchTool

        providers = []
        for entry in getattr(search_cfg, "providers", []):
            ptype = getattr(entry, "type", "ddgs")
            timeout = getattr(entry, "timeout", 10.0)
            key_env = getattr(entry, "key_env", "")
            if ptype == "ddgs":
                providers.append(DuckDuckGoSearchProvider(timeout=int(timeout)))
            elif ptype == "brave":
                api_key = os.environ.get(key_env, "")
                if api_key:
                    providers.append(BraveSearchProvider(api_key, timeout=timeout))
            elif ptype == "tavily":
                api_key = os.environ.get(key_env, "")
                if api_key:
                    providers.append(TavilySearchProvider(api_key, timeout=timeout))

        if providers:
            try:
                strategy = SearchStrategy(getattr(search_cfg, "strategy", "concurrent"))
            except ValueError:
                strategy = SearchStrategy.CONCURRENT
            registry.register(WebSearchTool.from_config(providers, strategy=strategy))

    # Web scrape — enabled by default via scrape.enabled (default: True).
    # Cookie store persists at ~/.nexus/cookies/ for cross-session auth.
    if scrape_cfg and getattr(scrape_cfg, "enabled", False):
        from pathlib import Path

        from loom.store.cookies import FilesystemCookieStore
        from loom.tools.scrape import WebScrapeTool

        cookie_dir = Path.home() / ".nexus" / "cookies"
        cookie_store = FilesystemCookieStore(cookie_dir)
        registry.register(
            WebScrapeTool.from_config(
                mode=getattr(scrape_cfg, "mode", "auto"),
                cookie_store=cookie_store,
                headless=getattr(scrape_cfg, "headless", True),
                timeout=getattr(scrape_cfg, "timeout", 30),
                max_content_bytes=getattr(scrape_cfg, "max_content_bytes", 102400),
            )
        )

    return registry
