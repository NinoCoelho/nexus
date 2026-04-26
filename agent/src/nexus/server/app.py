"""FastAPI application factory for Nexus.

``create_app()`` wires shared state onto ``app.state``, mounts all routers,
registers the lifespan hook, and attaches CORS middleware.
All endpoint implementations live in ``server/routes/``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


_AUTH_FREE_PATHS = {"/health"}


class LoopbackOrTokenMiddleware(BaseHTTPMiddleware):
    """Allow loopback clients without auth; require a bearer token otherwise.

    Token is read from the env var ``NEXUS_ACCESS_TOKEN`` at startup. Empty/unset
    → no auth required at all (back-compat with the dev `nexus serve` flow).
    Loopback clients (127.0.0.1 / ::1) always bypass the check, so the bundled
    UI talking to its own server keeps working without a token.
    """

    def __init__(self, app, token: str) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next):
        if not self._token or request.url.path in _AUTH_FREE_PATHS:
            return await call_next(request)
        client_host = request.client.host if request.client else ""
        if client_host in ("127.0.0.1", "::1", "localhost"):
            return await call_next(request)
        # Accept token via Authorization: Bearer <t> or ?token=<t>
        auth = request.headers.get("authorization", "")
        provided = ""
        if auth.lower().startswith("bearer "):
            provided = auth[7:].strip()
        if not provided:
            provided = request.query_params.get("token", "")
        if provided != self._token:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

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
        # Kill orphan llama-server processes from previous crashed runs
        # BEFORE restarting models, so we don't accumulate duplicates.
        try:
            from ..local_llm import manager as _local_mgr
            reaped = await asyncio.to_thread(_local_mgr.reap_orphans)
            if reaped:
                log.info("[local_llm] reaped %d orphan(s): %s", len(reaped), reaped)
        except Exception:
            log.exception("local_llm orphan reap failed")

        # Restart any local-* models the user had enabled in a prior run.
        # Each gets a fresh port; we refresh config and rebuild the registry
        # so the agent sees the live URLs without the user opening Settings.
        # Models whose GGUF is gone (or that fail to spawn) get pruned instead.
        try:
            from ..local_llm import manager as _local_mgr

            def _restart_blocking() -> int:
                return _local_mgr.restart_local_models()

            started = await asyncio.to_thread(_restart_blocking)
            if started > 0:
                from ..config_file import load as _load_cfg
                from .routes.config import _rebuild_registry
                _rebuild_registry(_load_cfg(), mutable_state, agent)
                log.info("[local_llm] restarted %d local model(s) at startup", started)
            else:
                # No live entries to restart — strip any dangling local-*
                # entries left in config from an unclean shutdown.
                _local_mgr.cleanup_stale_config()
        except Exception:
            log.exception("local_llm restart failed")
        if graphrag_cfg is not None:
            try:
                from ..agent.graphrag_manager import initialize

                log.info("Initializing GraphRAG engine...")
                await initialize(nexus_cfg)
                log.info("GraphRAG engine initialized")
            except Exception:
                log.exception("GraphRAG initialization failed")
            # Warm the builtin embedder ONNX cache in the background so the
            # first reindex doesn't block on a 24 MB download. Fire-and-forget;
            # errors are non-fatal — the model will just download on demand.
            async def _prefetch_builtin() -> None:
                try:
                    from ..agent.builtin_embedder import get_builtin_embedder
                    await get_builtin_embedder().embed(["__warmup__"])
                    log.info("[startup] builtin embedder cache warm")
                except Exception:
                    log.warning("[startup] builtin embedder prefetch failed", exc_info=True)
            asyncio.create_task(_prefetch_builtin())

        # ── calendar bootstrap + heartbeat scheduler ─────────────────────────
        scheduler = None
        try:
            from .. import vault_calendar
            from ..calendar_runtime import (
                set_dispatcher as _set_cal_dispatcher,
                set_notifier as _set_cal_notifier,
            )
            from ..heartbeat_drivers import DRIVERS_DIR
            from pathlib import Path
            from loom.heartbeat import (
                HeartbeatRegistry,
                HeartbeatScheduler,
                HeartbeatStore,
            )

            vault_calendar.ensure_default_calendar()
            vault_calendar.sweep_missed(grace_minutes=5)

            # Hand the calendar driver a way to invoke vault dispatch. Bound
            # here (not at module-load time) so it sees the live agent + store.
            from .routes.vault_dispatch import _dispatch_impl as _cal_dispatch

            async def _calendar_dispatcher(*, path: str, event_id: str, mode: str = "background") -> dict:
                return await _cal_dispatch(
                    path=path, card_id=None, event_id=event_id,
                    mode=mode, a=agent, store=sessions,
                )
            _set_cal_dispatcher(_calendar_dispatcher)

            # Notifier — fire-and-forget calendar_alert publishes onto the
            # cross-session SSE channel. The UI's useCalendarAlerts hook
            # subscribes to /notifications/events and surfaces a toast.
            from .events import SessionEvent

            def _calendar_notifier(payload: dict) -> None:
                # Use a synthetic session id so the fanout works through the
                # store's per-session router; the UI ignores session_id for
                # calendar_alert events.
                sessions.publish(
                    "__calendar__",
                    SessionEvent(kind="calendar_alert", data=payload),
                )
            _set_cal_notifier(_calendar_notifier)

            heartbeats_dir = Path("~/.nexus/heartbeats").expanduser()
            heartbeats_dir.mkdir(parents=True, exist_ok=True)
            db_path = Path("~/.nexus/heartbeat.db").expanduser()
            registry = HeartbeatRegistry(
                heartbeats_dir=heartbeats_dir,
                additional_dirs=[DRIVERS_DIR],
            )
            registry.scan()
            store = HeartbeatStore(db_path)

            async def _noop_run_fn(instructions: str, messages):  # noqa: ANN001
                # Driver dispatches inline and returns events=[], so this is
                # never called. Return a placeholder AgentTurn-shaped object
                # in case loom's contract changes.
                from loom.loop import AgentTurn
                return AgentTurn(reply="", input_tokens=0, output_tokens=0, tool_calls=0)

            scheduler = HeartbeatScheduler(
                registry, store, run_fn=_noop_run_fn, tick_interval=60.0,
            )
            scheduler.start()
            app.state.heartbeat_scheduler = scheduler
            log.info("heartbeat scheduler started (calendar_trigger registered)")
        except Exception:
            log.exception("heartbeat / calendar bootstrap failed")

        try:
            yield
        finally:
            if scheduler is not None:
                try:
                    scheduler.stop()
                except Exception:
                    log.exception("heartbeat scheduler stop failed")
            try:
                from ..local_llm import manager as _local_mgr
                _local_mgr.stop_all()
            except Exception:
                log.exception("local_llm stop_all failed")
            from ..agent.memory import close_memory_store
            close_memory_store()
            await agent.aclose()

    app = FastAPI(title="nexus", version="0.1.0", lifespan=lifespan)
    _access_token = os.environ.get("NEXUS_ACCESS_TOKEN", "")
    if _access_token:
        app.add_middleware(LoopbackOrTokenMiddleware, token=_access_token)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://localhost:\d+|http://127\.0\.0\.1:\d+",
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
    from .routes.vault_calendar import router as vault_calendar_router
    from .routes.vault_datatable import router as vault_datatable_router
    from .routes.vault_dispatch import router as vault_dispatch_router
    from .routes.config import router as config_router
    from .routes.providers import router as providers_router
    from .routes.models import router as models_router
    from .routes.share import router as share_router
    from .routes.local_llm import router as local_llm_router
    from .routes.notifications import router as notifications_router
    from .routes.push import router as push_router

    app.include_router(chat_router)
    app.include_router(chat_stream_router)
    app.include_router(settings_router)
    app.include_router(sessions_router)
    app.include_router(sessions_vault_router)
    app.include_router(graph_router)
    app.include_router(insights_router)
    app.include_router(vault_router)
    app.include_router(vault_kanban_router)
    app.include_router(vault_calendar_router)
    app.include_router(vault_datatable_router)
    app.include_router(vault_dispatch_router)
    app.include_router(config_router)
    app.include_router(providers_router)
    app.include_router(models_router)
    app.include_router(share_router)
    app.include_router(local_llm_router)
    app.include_router(notifications_router)
    app.include_router(push_router)

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
