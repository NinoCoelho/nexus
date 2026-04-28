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
from starlette.responses import JSONResponse, RedirectResponse


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
})

# API surface that must require a cookie when reached through the tunnel. This
# is an allowlist of *prefixes*; everything not matching is treated as static
# UI content (SPA shell, /assets, /icons, /manifest.json, /favicon.ico, etc.)
# and allowed through. Static UI is harmless without auth — the data routes
# below carry all the actual session state.
TUNNEL_PROTECTED_PREFIXES = (
    "/chat", "/sessions", "/vault", "/skills", "/config", "/providers",
    "/models", "/routing", "/graph", "/graphrag", "/insights", "/share",
    "/local", "/notifications", "/push", "/transcribe", "/audio", "/health",
)


def _is_proxied(request: Request) -> bool:
    """Heuristic: the request hopped through a reverse proxy (i.e. came via the tunnel).

    cloudflared, ngrok, and most edge proxies set ``x-forwarded-for`` and
    ``x-forwarded-proto``. Direct loopback connections do not. This is what lets
    us bypass auth for the bundled UI talking to its own server while still
    enforcing it for the same loopback IP when the connection traversed a tunnel.
    """
    h = request.headers
    return bool(
        h.get("x-forwarded-for")
        or h.get("x-forwarded-host")
        or h.get("cf-ray")              # cloudflared / Cloudflare edge
        or h.get("cf-connecting-ip")    # cloudflared / Cloudflare edge
        or h.get("ngrok-trace-id")      # legacy: still safe to honor
    )


def _tunnel_path_requires_auth(path: str) -> bool:
    """Decide whether a tunnel-side request must present a valid cookie.

    Returns True for protected API surfaces and the loopback-only tunnel admin
    endpoints (which will then 403 from inside the route). False for the public
    redeem/probe endpoints and for any non-API path that's most likely static
    UI content.
    """
    if path in TUNNEL_PUBLIC_PATHS:
        return False
    if path.startswith("/tunnel/"):
        # /tunnel/start, /stop, /status, /install — admin. Require a cookie
        # so the request gets to the route, where _require_loopback will 403.
        return True
    return any(path.startswith(p) for p in TUNNEL_PROTECTED_PREFIXES)


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
    # Give the agent the SessionStore so the streaming loop can persist
    # parked HITL snapshots and the resume entry-point can rehydrate them.
    agent._sessions = sessions

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

        # Reap orphaned cloudflared tunnels from a previous .app instance.
        # Without this, the in-memory TunnelManager boots with active=False
        # while a re-parented cloudflared keeps publishing the old URL — any
        # phone hitting that URL then trips the no-active-tunnel 401 fallback,
        # bypassing the redirect-to-/ branch.
        try:
            from ..tunnel import cloudflared_provider
            killed = cloudflared_provider.cleanup_orphans()
            if killed:
                log.info("cleaned up %d orphan cloudflared process(es) on startup", killed)
        except Exception:
            log.exception("cloudflared orphan cleanup failed")
        # ``NEXUS_SKIP_LOCAL_LLM_RESTART=1`` skips both the orphan reap and
        # the model-restart path. Tests set it because:
        #   - reap_orphans() walks the host process table and kills any
        #     llama-server it finds whose parent isn't the test process —
        #     which would terminate the real daemon's running models.
        #   - restart_local_models() reads the user's real
        #     ~/.nexus/config.toml, blocks for seconds spawning GGUFs the
        #     test never asked for, and may rewrite the config when a GGUF
        #     can't be found.
        skip_local_llm = bool(os.environ.get("NEXUS_SKIP_LOCAL_LLM_RESTART"))

        # Kill orphan llama-server processes from previous crashed runs
        # BEFORE restarting models, so we don't accumulate duplicates.
        if not skip_local_llm:
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
        if not skip_local_llm:
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
        # ── parked HITL sweep (durable async ask_user) ──────────────────────
        # Re-publish ``user_request`` for any rows that were left parked when
        # the server stopped, so connected clients re-queue them in the bell.
        # Also expire rows whose deadline_at has passed (rare — most parked
        # requests are open-ended). Best-effort; failures don't block boot.
        try:
            from .events import SessionEvent as _SE
            from datetime import datetime, timezone

            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            re_published = 0
            for row in sessions.list_all_pending():
                deadline = row.get("deadline_at")
                if deadline and deadline < now_iso:
                    sessions.cancel_hitl_pending(
                        row["request_id"], reason="expired",
                    )
                    sessions.publish(
                        row["session_id"],
                        _SE(
                            kind="user_request_cancelled",
                            data={
                                "request_id": row["request_id"],
                                "reason": "expired",
                            },
                        ),
                    )
                    continue
                ev_data: dict[str, Any] = {
                    "request_id": row["request_id"],
                    "prompt": row["prompt"],
                    "kind": row["kind"],
                    "choices": row.get("choices"),
                    "default": row.get("default"),
                    "timeout_seconds": row.get("timeout_seconds"),
                }
                if row.get("kind") == "form":
                    ev_data["fields"] = row.get("fields")
                    ev_data["form_title"] = row.get("form_title")
                    ev_data["form_description"] = row.get("form_description")
                sessions.publish(
                    row["session_id"], _SE(kind="user_request", data=ev_data),
                )
                re_published += 1
            if re_published:
                log.info(
                    "[hitl] re-published %d parked request(s) at startup",
                    re_published,
                )
            sessions.trim_hitl_pending(keep_days=30)
        except Exception:
            log.exception("hitl parked sweep failed")

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

    # Single-user app, not a third-party API — disable FastAPI's auto-generated
    # /docs, /redoc, /openapi.json. Otherwise anyone with the tunnel URL could
    # enumerate the full API surface, which is unnecessary information leakage.
    app = FastAPI(
        title="nexus",
        version="0.1.0",
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
    app.add_middleware(LoopbackOrTokenMiddleware, access_token=_access_token)
    app.add_middleware(SecurityHeadersMiddleware)
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
    from .routes.chat_slash import router as chat_slash_router
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
    from .routes.tunnel import router as tunnel_router

    app.include_router(chat_router)
    app.include_router(chat_slash_router)
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
    app.include_router(tunnel_router)

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
