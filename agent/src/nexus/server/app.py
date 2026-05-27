"""FastAPI application factory for Nexus.

``create_app()`` wires shared state onto ``app.state``, mounts all routers,
registers the lifespan hook, and attaches CORS middleware.
All endpoint implementations live in ``server/routes/``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse

from ..agent.ask_user_tool import AskUserHandler
from ..agent.context import CURRENT_SESSION_ID
from ..agent.loop import Agent
from ..skills.registry import SkillRegistry
from .app_lifespan import create_lifespan
from .app_subagents import create_subagent_runner
from .auth import AuthManager
from .deps import SessionStoreProxy
from .events import SessionEvent
from .job_tracker import JobTracker
from .middleware import MultiUserAuthMiddleware
from .session_store import SessionStore
from .settings import SettingsStore
from .user_store import UserStore


_AUTH_FREE_PATHS = {"/health"}
# Cookie name used to carry the tunnel token. HttpOnly + SameSite=Strict so
# it survives SSE (which can't set headers) but never leaks via JS or cross-site.
TUNNEL_COOKIE = "nexus_tunnel_token"

# Endpoints the phone needs to reach BEFORE it has a cookie, so the login flow
# can complete: probe auth, exchange code for cookie. Listed explicitly so the
# tunnel-traffic gate has a tight, auditable allowlist.
TUNNEL_PUBLIC_PATHS = frozenset({
    "/tunnel/redeem",
    "/tunnel/auth-status",
    "/webhook",
    "/workflow/trigger",
})

# API surface that must require a cookie when reached through the tunnel. This
# is an allowlist of *prefixes*; everything not matching is treated as static
# UI content (SPA shell, /assets, /icons, /manifest.json, /favicon.ico, etc.)
# and allowed through. Static UI is harmless without auth — the data routes
# below carry all the actual session state.
TUNNEL_PROTECTED_PREFIXES = (
    "/chat", "/sessions", "/vault", "/skills", "/config", "/providers",
    "/catalog", "/auth", "/models", "/routing", "/graph", "/graphrag",
    "/share", "/local", "/notifications", "/push",
    "/transcribe", "/audio", "/health", "/heartbeat", "/cookies",
    "/dream", "/mcp", "/jobs", "/update", "/workflows", "/projects",
    "/credentials",
)


def _is_proxied(request: Request) -> bool:
    from .middleware import _is_proxied as _check
    return _check(request)


def _tunnel_path_requires_auth(path: str) -> bool:
    """Decide whether a tunnel-side request must present a valid cookie.

    Returns True for protected API surfaces and the loopback-only tunnel admin
    endpoints (which will then 403 from inside the route). False for the public
    redeem/probe endpoints and for any non-API path that's most likely static
    UI content.
    """
    if path in TUNNEL_PUBLIC_PATHS:
        return False
    if path.startswith("/webhook/"):
        return False
    if path.startswith("/workflow/trigger/"):
        return False
    if path.startswith("/tunnel/"):
        # /tunnel/start, /stop, /status, /install — admin. Require a cookie
        # so the request gets to the route, where _require_loopback will 403.
        return True
    return any(path.startswith(p) for p in TUNNEL_PROTECTED_PREFIXES)


class FeatureGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        from ..features import feature_for_route, is_enabled
        feat = feature_for_route(request.url.path)
        if feat and not is_enabled(feat):
            return JSONResponse(
                {"detail": f"Feature '{feat}' is not available on your plan"},
                status_code=403,
            )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Set baseline security response headers on every response.

    These don't replace the auth gate, they harden the *browser side* of the
    interaction:
      * X-Content-Type-Options=nosniff blocks MIME-sniffing attacks.
      * X-Frame-Options=DENY prevents clickjacking via iframe embedding.
      * Referrer-Policy=same-origin keeps the tunnel URL from leaking via
        outbound link clicks (the trycloudflare hostname itself is sensitive).
      * Permissions-Policy disables sensors / payment / autoplay we never use.

    No CSP yet — adding one without breaking mermaid / katex / dynamic imports
    is a separate effort. SameSite=Strict + HttpOnly on the auth cookie already
    blocks the worst classes of XSS exfil.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(self), camera=(), payment=()",
        )
        return response


class LoopbackOrTokenMiddleware(BaseHTTPMiddleware):
    """Auth gate combining the legacy access-token flow with the tunnel-token flow.

    Two independent token sources are accepted:

    1. ``NEXUS_ACCESS_TOKEN`` (env, set at startup): protects the server when
       running on a non-loopback bind. This is the back-compat path; empty/unset
       means no enforcement for *direct* clients.
    2. The dynamic tunnel token (``nexus.tunnel.manager``): generated when the
       user activates sharing. Required on every request that arrived via the
       tunnel (detected by proxy headers — see ``_is_proxied``).

    Direct loopback clients with no proxy headers always bypass auth, so the
    bundled UI on the user's own machine keeps working with zero config.
    """

    def __init__(self, app, access_token: str) -> None:
        super().__init__(app)
        self._access_token = access_token

    async def dispatch(self, request: Request, call_next):
        from ..tunnel import get_manager  # local import to avoid cycles
        tunnel = get_manager()

        path = request.url.path
        client_host = request.client.host if request.client else ""
        is_loopback = client_host in ("127.0.0.1", "::1", "localhost")
        proxied = _is_proxied(request)

        # Pull a cookie/header token candidate. Used to authorize protected
        # tunnel traffic; the short access code never travels through here —
        # it goes directly to /tunnel/redeem in a POST body.
        provided = ""
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            provided = auth[7:].strip()
        if not provided:
            provided = request.cookies.get(TUNNEL_COOKIE, "")

        # Tunnel path. Two policies depending on what's being accessed:
        #   * Static UI / login probes / redeem  → pass through (route enforces).
        #   * Protected API + admin              → require valid cookie.
        # The split is what lets the SPA load on the phone before the user has
        # entered the access code: HTML/JS/CSS comes back fine, then the SPA
        # calls /tunnel/auth-status, sees `requires_redeem: true`, and shows
        # the login form. POST /tunnel/redeem then installs the cookie.
        if tunnel.is_active() and proxied:
            if not _tunnel_path_requires_auth(path):
                return await call_next(request)
            if not tunnel.validate_token(provided):
                # Top-level browser navigation (Accept: text/html) sees the SPA
                # shell instead of raw JSON: deep-link refreshes and stale-cookie
                # reopens then land on the pairing screen via AuthGate. XHR-style
                # callers (Accept: application/json or */*) still get a 401 the
                # SPA's fetch interceptor can react to.
                #
                # Cache-Control: no-store on both branches — iOS Safari otherwise
                # caches 4xx and even 30x responses on the device, which means a
                # phone that ever saw the old 401 (before this redirect existed)
                # would keep serving it until the user manually cleared site data.
                if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
                    r = RedirectResponse("/", status_code=307)
                    r.headers["Cache-Control"] = "no-store"
                    return r
                return JSONResponse(
                    {"detail": "unauthorized"},
                    status_code=401,
                    headers={"Cache-Control": "no-store"},
                )
            return await call_next(request)

        if path in _AUTH_FREE_PATHS:
            return await call_next(request)

        # Direct loopback (no proxy headers) — bundled UI talking to itself.
        if is_loopback and not proxied:
            return await call_next(request)

        # Legacy NEXUS_ACCESS_TOKEN gate for non-loopback binds.
        if self._access_token:
            if provided != self._access_token:
                # Fall back to the legacy ?token= flow only on the non-tunnel
                # path; tunnel traffic should never carry secrets in the URL.
                qp_token = request.query_params.get("token", "")
                if qp_token != self._access_token:
                    return JSONResponse(
                        {"detail": "unauthorized"},
                        status_code=401,
                        headers={"Cache-Control": "no-store"},
                    )
            return await call_next(request)

        # Non-loopback request, tunnel inactive, no access token configured →
        # close it down rather than silently allowing it.
        return JSONResponse(
            {"detail": "unauthorized"},
            status_code=401,
            headers={"Cache-Control": "no-store"},
        )

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
    job_tracker = JobTracker()

    multi_user = bool(nexus_cfg and getattr(nexus_cfg, "server", None) and nexus_cfg.server.multi_user)
    user_store = UserStore() if multi_user else None
    auth_manager = AuthManager() if multi_user else None

    session_registry = None
    if multi_user:
        from .session_store.registry import UserSessionRegistry
        session_registry = UserSessionRegistry()

    # Proxy that resolves the correct per-user SessionStore based on
    # CURRENT_SESSION_ID.  In single-user mode this is a no-op wrapper
    # that always returns the default store — identical to before.
    # Used by agent._sessions, AskUserHandler, _trace, etc.
    _app_state_ns = type("_AppState", (), {
        "multi_user": multi_user,
        "session_registry": session_registry,
        "user_store": user_store,
    })()
    store_proxy = SessionStoreProxy(sessions, _app_state_ns)

    def _publish_job_event(kind: str, data: dict[str, Any]) -> None:
        if multi_user and session_registry is not None:
            for _uid, user_store_inst in session_registry.all_stores().items():
                user_store_inst.publish(
                    "__jobs__",
                    SessionEvent(kind=kind, data=data),
                )
        else:
            sessions.publish(
                "__jobs__",
                SessionEvent(kind=kind, data=data),
            )

    # Mutable dict passed by reference into all route handlers that need to
    # read or update cfg/prov_reg (config, providers, models, routing).
    mutable_state: dict[str, Any] = {"cfg": nexus_cfg, "prov_reg": provider_registry}

    # Wire the HITL primitive. The ``AskUserHandler`` reads ``yolo_mode``
    # on every call via this getter — a callable (not a snapshot) so
    # toggling the setting takes effect on the next ``ask_user`` without
    # restarting the server. Attached to the agent so its loop's
    # ``_tools()`` / ``_handle()`` branches pick it up.
    ask_user_handler = AskUserHandler(
        session_store=store_proxy,
        yolo_mode_getter=lambda: settings_store.get().yolo_mode,
    )

    def _make_terminal_output_callback(
        session_store: Any,
        proc_registry: dict[str, asyncio.subprocess.Process],
    ) -> Callable[[str, str], Awaitable[None]]:
        async def _on_terminal_output(stdout: str, stderr: str) -> None:
            session_id = CURRENT_SESSION_ID.get()
            if session_id is None:
                return
            session_store.publish(
                session_id,
                SessionEvent(
                    kind="terminal_output",
                    data={
                        "stdout": stdout,
                        "stderr": stderr,
                        "call_id": getattr(_on_terminal_output, "_call_id", ""),
                    },
                ),
            )
        return _on_terminal_output

    def _make_proc_register(
        proc_registry: dict[str, asyncio.subprocess.Process],
    ) -> Callable[[asyncio.subprocess.Process], None]:
        def _register(proc: asyncio.subprocess.Process) -> None:
            session_id = CURRENT_SESSION_ID.get() or ""
            call_id = ""
            handler = agent._terminal_handler
            if handler is not None and hasattr(handler, "_on_output"):
                cb = handler._on_output
                call_id = getattr(cb, "_call_id", "")
            if not call_id:
                return
            key = f"{session_id}:{call_id}"
            proc_registry[key] = proc
        return _register

    def _make_proc_unregister(
        proc_registry: dict[str, asyncio.subprocess.Process],
    ) -> Callable[[], None]:
        def _unregister() -> None:
            session_id = CURRENT_SESSION_ID.get() or ""
            call_id = ""
            handler = agent._terminal_handler
            if handler is not None and hasattr(handler, "_on_output"):
                cb = handler._on_output
                call_id = getattr(cb, "_call_id", "")
            if not call_id:
                return
            proc_registry.pop(f"{session_id}:{call_id}", None)
        return _unregister

    # Late-bind the handler onto the agent. Constructed-outside-the-app
    # callers (``main.py``) don't know about HITL; constructing the
    # handler here keeps all the server-side wiring in one place.
    # The terminal tool is loom-native (loom.tools.terminal.TerminalTool)
    # and reads its session id from the shared CURRENT_SESSION_ID ContextVar.
    from loom.tools.terminal import TerminalTool

    # Process registry for live terminal monitoring / kill support.
    # Keyed by "session_id:tool_call_id" so the kill endpoint and the
    # streaming callback can locate the running subprocess.
    _terminal_procs: dict[str, asyncio.subprocess.Process] = {}

    _on_term_output = _make_terminal_output_callback(store_proxy, _terminal_procs)
    _proc_reg = _make_proc_register(_terminal_procs)
    _proc_unreg = _make_proc_unregister(_terminal_procs)

    agent._ask_user_handler = ask_user_handler
    agent._terminal_handler = TerminalTool(
        broker=store_proxy.broker,
        yolo_getter=lambda: settings_store.get().yolo_mode,
        on_output=_on_term_output,
        proc_register=_proc_reg,
        proc_unregister=_proc_unreg,
    )

    # notify_user — fire-and-forget status pings the agent emits during
    # long operations (TTS when the originating message was voice; toast
    # in every case). The handler depends on the SessionStore both for
    # publishing on the per-session SSE channel and for reading the
    # original input_mode (stashed on the store by chat_stream).
    from ..agent.notify_user_tool import NotifyUserHandler
    agent._notify_user_handler = NotifyUserHandler(session_store=store_proxy)
    agent._sessions = store_proxy

    # Trace callback routes every agent event (iter, tool_call,
    # tool_result, reply) into the SSE subscriber fanout for whichever
    # session is currently running the turn. Reads the session_id from
    # a contextvar set in the /chat handler — the Agent stays
    # session-agnostic.
    def _trace(kind: str, data: dict[str, Any]) -> None:
        session_id = CURRENT_SESSION_ID.get()
        if session_id is None:
            return
        store_proxy.publish(session_id, SessionEvent(kind=kind, data=data))

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

    lifespan = create_lifespan({
        "graphrag_cfg": graphrag_cfg,
        "nexus_cfg": nexus_cfg,
        "mutable_state": mutable_state,
        "agent": agent,
        "sessions": sessions,
        "multi_user": multi_user,
        "session_registry": session_registry,
        "store_proxy": store_proxy,
        "job_tracker": job_tracker,
        "publish_job_event": _publish_job_event,
    })

    # Single-user app, not a third-party API — disable FastAPI's auto-generated
    # /docs, /redoc, /openapi.json. Otherwise anyone with the tunnel URL could
    # enumerate the full API surface, which is unnecessary information leakage.
    app = FastAPI(
        title="nexus",
        version=__import__("nexus").__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    # Middleware is always installed: the legacy NEXUS_ACCESS_TOKEN path is
    # opt-in (empty string disables it for direct loopback), but the tunnel
    # auth path must remain reachable on every request so it can enforce the
    # token on traffic that traversed the public tunnel.
    _access_token = os.environ.get("NEXUS_ACCESS_TOKEN", "")
    if multi_user:
        app.add_middleware(
            MultiUserAuthMiddleware,
            auth_manager=auth_manager,
            user_store=user_store,
        )
    else:
        app.add_middleware(LoopbackOrTokenMiddleware, access_token=_access_token)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(FeatureGateMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://localhost:\d+|http://127\.0\.0\.1:\d+",
        allow_credentials=True,
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
    app.state.terminal_procs = _terminal_procs
    app.state.job_tracker = job_tracker
    app.state.multi_user = multi_user
    app.state.user_store = user_store
    app.state.auth_manager = auth_manager
    app.state.session_registry = session_registry

    from .kanban_queue import init_queue
    init_queue()
    log.info("kanban queue initialised")

    # ── mount routers ──────────────────────────────────────────────────────────
    from .routes.chat import router as chat_router
    from .routes.chat_slash import router as chat_slash_router
    from .routes.chat_stream import router as chat_stream_router
    from .routes.settings import router as settings_router
    from .routes.sessions import router as sessions_router
    from .routes.sessions_vault import router as sessions_vault_router
    from .routes.graph import router as graph_router
    from .routes.vault import router as vault_router
    from .routes.vault_kanban import router as vault_kanban_router
    from .routes.vault_calendar import router as vault_calendar_router
    from .routes.vault_datatable import router as vault_datatable_router
    from .routes.vault_dashboard import router as vault_dashboard_router
    from .routes.vault_dispatch import router as vault_dispatch_router
    from .routes.vault_history import router as vault_history_router
    from .routes.config import router as config_router
    from .routes.catalog import router as catalog_router
    from .routes.local_creds import router as local_creds_router
    from .routes.oauth import router as oauth_router
    from .routes.providers import router as providers_router
    from .routes.credentials import router as credentials_router
    from .routes.models import router as models_router
    from .routes.share import router as share_router
    from .routes.local_llm import router as local_llm_router
    from .routes.notifications import router as notifications_router
    from .routes.push import router as push_router
    from .routes.skill_wizard import router as skill_wizard_router
    from .routes.tunnel import router as tunnel_router
    from .routes.tts import router as tts_router
    from .routes.nexus_account import router as nexus_account_router
    from .routes.webhook import router as webhook_router
    from .routes.broker import router as broker_router
    from .routes.heartbeat import router as heartbeat_router
    from .routes.cookies import router as cookies_router
    from .routes.dream import router as dream_router
    from .routes.mcp import router as mcp_router
    from .routes.jobs import router as jobs_router
    from .routes.vault_import import router as vault_import_router
    from .routes.update import router as update_router
    from .routes.workflows import router as workflows_router
    from .routes.projects import router as projects_router

    app.include_router(chat_router)
    app.include_router(chat_slash_router)
    app.include_router(chat_stream_router)
    app.include_router(settings_router)
    app.include_router(sessions_router)

    if multi_user:
        from .routes.auth import router as auth_router
        from .routes.admin import router as admin_router
        from .routes.vault_share import router as vault_share_router
        app.include_router(auth_router)
        app.include_router(admin_router)
        app.include_router(vault_share_router)
        if user_store and not user_store.has_any_users():
            log.info("Multi-user mode: no users found. Sign in with a Nexus account to create the admin.")
    app.include_router(sessions_vault_router)
    app.include_router(graph_router)
    app.include_router(vault_router)
    app.include_router(vault_kanban_router)
    app.include_router(vault_calendar_router)
    app.include_router(vault_datatable_router)
    app.include_router(vault_dashboard_router)
    app.include_router(vault_dispatch_router)
    app.include_router(vault_history_router)
    app.include_router(vault_import_router)
    app.include_router(config_router)
    app.include_router(catalog_router)
    app.include_router(oauth_router)
    app.include_router(local_creds_router)
    app.include_router(providers_router)
    app.include_router(credentials_router)
    app.include_router(models_router)
    app.include_router(share_router)
    app.include_router(local_llm_router)
    app.include_router(notifications_router)
    app.include_router(push_router)
    app.include_router(skill_wizard_router)
    app.include_router(tunnel_router)
    app.include_router(tts_router)
    app.include_router(nexus_account_router)
    app.include_router(webhook_router)
    app.include_router(broker_router)
    app.include_router(heartbeat_router)
    app.include_router(cookies_router)
    app.include_router(dream_router)
    app.include_router(mcp_router)
    app.include_router(jobs_router)
    app.include_router(update_router)
    app.include_router(workflows_router)
    app.include_router(projects_router)

    # ── wire the dispatch_card agent tool ──────────────────────────────────────
    # The dispatch_card tool needs to call _dispatch_impl with the live agent
    # and sessions objects. We bind a closure here after the router is set up.
    from .routes.vault_dispatch import _dispatch_impl

    async def _agent_dispatcher(*, path: str, card_id: str | None, mode: str) -> dict:
        return await _dispatch_impl(path=path, card_id=card_id, mode=mode, a=agent, store=store_proxy)

    agent._dispatcher = _agent_dispatcher

    # ── wire the spawn_subagents agent tool ───────────────────────────────────
    agent._handlers.subagent_runner = create_subagent_runner(
        agent=agent,
        store_proxy=store_proxy,
        job_tracker=job_tracker,
        publish_job_event=_publish_job_event,
    )

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
                a=agent, store=store_proxy,
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
    #
    # Cache policy: hashed assets under /assets/ are content-addressed and
    # can be cached forever; index.html / sw.js / manifest.json must always
    # be revalidated so a fresh build's new chunk hashes are picked up
    # instead of clients holding onto vanished filenames.
    async def _spa(request: Request) -> Response:
        ui_path = request.path_params.get("ui_path", "") or ""
        if ui_path:
            try:
                target = (dist / ui_path).resolve()
                target.relative_to(dist_resolved)
                if target.is_file():
                    if ui_path.startswith("assets/"):
                        headers = {"Cache-Control": "public, max-age=31536000, immutable"}
                    else:
                        headers = {"Cache-Control": "no-cache"}
                    return FileResponse(target, headers=headers)
            except (ValueError, OSError):
                pass
        accept = request.headers.get("accept", "")
        if "text/html" in accept or accept == "" or accept == "*/*":
            return FileResponse(index_html, headers={"Cache-Control": "no-cache"})
        return Response(status_code=404)

    app.router.add_route("/{ui_path:path}", _spa, methods=["GET"], include_in_schema=False)
