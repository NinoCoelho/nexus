"""FastAPI application factory for Nexus.

``create_app()`` wires shared state onto ``app.state``, mounts all routers,
registers the lifespan hook, and attaches CORS middleware.
All endpoint implementations live in ``server/routes/``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..agent.ask_user_tool import AskUserHandler
from ..agent.context import CURRENT_SESSION_ID
from ..agent.loop import Agent
from ..skills.registry import SkillRegistry
from .events import SessionEvent
from .session_store import SessionStore
from .settings import SettingsStore

log = logging.getLogger(__name__)


def create_app(
    *,
    agent: Agent,
    registry: SkillRegistry,
    sessions: SessionStore | None = None,
    nexus_cfg: Any | None = None,
    provider_registry: Any | None = None,
    settings_store: SettingsStore | None = None,
    graphrag_cfg: Any | None = None,
) -> FastAPI:
    """Build and return the configured FastAPI application instance.

    Handles all server wiring: HITL (AskUserHandler, TerminalHandler),
    trace callback for SSE, kanban lane-change hook, bundled static UI,
    and registration of all routers from ``server/routes/``.

    Args:
        agent: Agent instance (created in main.py before the app).
        registry: Skill registry; passed to routes via app.state.
        sessions: Session store; created internally if not provided.
        nexus_cfg: Configuration loaded from ``~/.nexus/config.toml``.
        provider_registry: LLM provider registry; None disables multi-provider routing.
        settings_store: Runtime settings store; created internally if None.
        graphrag_cfg: If present, the GraphRAG engine is initialized during lifespan.

    Returns:
        FastAPI application ready to be served.
    """
    sessions = sessions or SessionStore()
    settings_store = settings_store or SettingsStore()

    # Mutable dict passed by reference into all route handlers that need to
    # read or update cfg/prov_reg (config, providers, models, routing).
    mutable_state: dict[str, Any] = {"cfg": nexus_cfg, "prov_reg": provider_registry}

    # Wire the HITL primitive. The ``AskUserHandler`` reads ``yolo_mode``
    # on every call via this getter — a callable (not a snapshot) so
    # toggling the setting takes effect on the next ``ask_user`` without
    # restarting the server. Attached to the agent so its loop's
    # ``_tools()`` / ``_handle()`` branches pick it up.
    ask_user_handler = AskUserHandler(
        session_store=sessions,
        yolo_mode_getter=lambda: settings_store.get().yolo_mode,
    )
    # Late-bind the handler onto the agent. Constructed-outside-the-app
    # callers (``main.py``) don't know about HITL; constructing the
    # handler here keeps all the server-side wiring in one place.
    from ..agent.terminal_tool import TerminalHandler

    agent._ask_user_handler = ask_user_handler
    agent._terminal_handler = TerminalHandler(ask_user_handler=ask_user_handler)

    # Trace callback routes every agent event (iter, tool_call,
    # tool_result, reply) into the SSE subscriber fanout for whichever
    # session is currently running the turn. Reads the session_id from
    # a contextvar set in the /chat handler — the Agent stays
    # session-agnostic.
    def _trace(kind: str, data: dict[str, Any]) -> None:
        session_id = CURRENT_SESSION_ID.get()
        if session_id is None:
            return
        sessions.publish(session_id, SessionEvent(kind=kind, data=data))

    # Install the trace hook without clobbering one the caller may
    # already have wired (main.py doesn't today, but a test might).
    if agent._trace is None:
        agent._trace = _trace
    else:
        existing = agent._trace
        def _compose(k: str, d: dict[str, Any]) -> None:
            existing(k, d)
            _trace(k, d)
        agent._trace = _compose

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        log.info("Lifespan starting (graphrag_cfg=%s)", "present" if graphrag_cfg is not None else "None")
        try:
            import asyncio
            from . import event_bus
            event_bus.set_loop(asyncio.get_running_loop())
        except Exception:
            log.exception("event_bus setup failed")
        if graphrag_cfg is not None:
            try:
                from ..agent.graphrag_manager import initialize

                log.info("Initializing GraphRAG engine...")
                await initialize(nexus_cfg)
                log.info("GraphRAG engine initialized")
            except Exception:
                log.exception("GraphRAG initialization failed")
        try:
            yield
        finally:
            from ..agent.memory import close_memory_store
            close_memory_store()
            await agent.aclose()

    app = FastAPI(title="nexus", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://localhost:\d+",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Populate app.state so all Depends() getters in deps.py can read them.
    app.state.agent = agent
    app.state.sessions = sessions
    app.state.settings_store = settings_store
    app.state.registry = registry
    app.state.mutable_state = mutable_state
    app.state.graphrag_cfg = graphrag_cfg
    app.state.ask_user_handler = ask_user_handler

    # ── mount routers ──────────────────────────────────────────────────────────
    from .routes.chat import router as chat_router
    from .routes.chat_stream import router as chat_stream_router
    from .routes.settings import router as settings_router
    from .routes.sessions import router as sessions_router
    from .routes.sessions_vault import router as sessions_vault_router
    from .routes.graph import router as graph_router
    from .routes.insights import router as insights_router
    from .routes.vault import router as vault_router
    from .routes.vault_kanban import router as vault_kanban_router
    from .routes.vault_datatable import router as vault_datatable_router
    from .routes.vault_dispatch import router as vault_dispatch_router
    from .routes.config import router as config_router
    from .routes.providers import router as providers_router
    from .routes.models import router as models_router
    from .routes.share import router as share_router

    app.include_router(chat_router)
    app.include_router(chat_stream_router)
    app.include_router(settings_router)
    app.include_router(sessions_router)
    app.include_router(sessions_vault_router)
    app.include_router(graph_router)
    app.include_router(insights_router)
    app.include_router(vault_router)
    app.include_router(vault_kanban_router)
    app.include_router(vault_datatable_router)
    app.include_router(vault_dispatch_router)
    app.include_router(config_router)
    app.include_router(providers_router)
    app.include_router(models_router)
    app.include_router(share_router)

    # ── wire the dispatch_card agent tool ──────────────────────────────────────
    # The dispatch_card tool needs to call _dispatch_impl with the live agent
    # and sessions objects. We bind a closure here after the router is set up.
    from .routes.vault_dispatch import _dispatch_impl

    async def _agent_dispatcher(*, path: str, card_id: str | None, mode: str) -> dict:
        return await _dispatch_impl(path=path, card_id=card_id, mode=mode, a=agent, store=sessions)

    agent._dispatcher = _agent_dispatcher

    # Lane-change hook: any cross-lane move (UI drag-drop via PATCH, agent
    # tool via kanban_manage, external API client) auto-dispatches the
    # destination lane's prompt if one is set. Cycle and depth guards via
    # the DISPATCH_CHAIN contextvar prevent runaway cascades.
    import asyncio
    from ..agent.context import DISPATCH_CHAIN

    MAX_DISPATCH_DEPTH = 5

    def _lane_change_hook(
        *,
        path: str,
        card_id: str,
        src_lane_id: str,
        dst_lane_id: str,
        dst_lane_prompt: str | None,
    ) -> None:
        if not dst_lane_prompt:
            return
        chain = DISPATCH_CHAIN.get()
        if card_id in chain:
            log.info(
                "lane_change_hook: skipping auto-dispatch for card %s "
                "(cycle: already in chain %s)", card_id, chain,
            )
            return
        if len(chain) >= MAX_DISPATCH_DEPTH:
            log.warning(
                "lane_change_hook: depth limit reached (%d) for card %s, chain=%s",
                MAX_DISPATCH_DEPTH, card_id, chain,
            )
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Sync test context with no event loop — silently skip.
            return
        loop.create_task(
            _dispatch_impl(
                path=path, card_id=card_id, mode="background",
                a=agent, store=sessions,
            )
        )

    from .. import vault_kanban as _vk
    _vk.set_lane_change_hook(_lane_change_hook)

    # ── transcription + bundled UI ─────────────────────────────────────────────
    from . import transcribe as _transcribe_mod
    _transcribe_mod.register(app)

    _mount_bundled_ui(app)

    return app


def _resolve_ui_dist() -> "Any | None":
    """Locate a built UI (``ui/dist``) — env override or sibling of frontend_dir."""
    import os
    from pathlib import Path

    env = os.environ.get("NEXUS_UI_DIST")
    if env:
        p = Path(env).expanduser()
        if (p / "index.html").is_file():
            return p

    from ..config import get_frontend_dir
    fe = get_frontend_dir()
    if fe is not None:
        dist = fe / "dist"
        if (dist / "index.html").is_file():
            return dist
    return None


def _mount_bundled_ui(app: FastAPI) -> None:
    """Serve a built UI from the backend if ``ui/dist`` is available.

    Registered after all API routes, so explicit routes always win. A catch-all
    GET handler serves static assets and falls back to ``index.html`` for
    client-side routing.
    """
    from starlette.requests import Request
    from starlette.responses import FileResponse, Response

    dist = _resolve_ui_dist()
    if dist is None:
        return

    index_html = dist / "index.html"
    dist_resolved = dist.resolve()
    log.info("Serving bundled UI from %s", dist)

    # Registered as a Starlette route (not a FastAPI route) so FastAPI's
    # parameter introspection doesn't try to coerce ``request`` into a query
    # param. Mounted last, so all explicit API routes win.
    async def _spa(request: Request) -> Response:
        ui_path = request.path_params.get("ui_path", "") or ""
        if ui_path:
            try:
                target = (dist / ui_path).resolve()
                target.relative_to(dist_resolved)
                if target.is_file():
                    return FileResponse(target)
            except (ValueError, OSError):
                pass
        accept = request.headers.get("accept", "")
        if "text/html" in accept or accept == "" or accept == "*/*":
            return FileResponse(index_html)
        return Response(status_code=404)

    app.router.add_route("/{ui_path:path}", _spa, methods=["GET"], include_in_schema=False)
