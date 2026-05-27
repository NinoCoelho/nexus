from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse

from .auth import AuthManager
from ..home import _ROOT, set_user_home

if TYPE_CHECKING:
    from .user_store import UserStore

log = logging.getLogger(__name__)

_SESSION_COOKIE = "nexus_session"

_PENDING_ALLOWED = frozenset({
    "/auth/status",
    "/auth/pending-status",
})

_PUBLIC_PATHS = frozenset({
    "/auth/invite",
    "/auth/register",
    "/auth/status",
    "/auth/pending-status",
    "/auth/nexus/verify",
    "/tunnel/redeem",
    "/tunnel/auth-status",
    "/webhook",
    "/health",
})


def _is_proxied(request: Request) -> bool:
    h = request.headers
    return bool(
        h.get("x-forwarded-for")
        or h.get("x-forwarded-host")
        or h.get("cf-ray")
        or h.get("cf-connecting-ip")
        or h.get("ngrok-trace-id")
    )


def _is_public_auth_path(path: str) -> bool:
    if path in _PUBLIC_PATHS:
        return True
    if path.startswith("/auth/invite/"):
        return True
    if path.startswith("/webhook/"):
        return True
    return False


def _is_api_path(path: str) -> bool:
    api_prefixes = (
        "/chat", "/sessions", "/vault", "/skills", "/config", "/providers",
        "/catalog", "/auth", "/models", "/routing", "/graph", "/graphrag",
        "/share", "/local", "/notifications", "/push",
        "/transcribe", "/audio", "/heartbeat", "/cookies",
        "/dream", "/mcp", "/jobs", "/admin",
    )
    if path.startswith("/tunnel/"):
        return True
    return any(path.startswith(p) for p in api_prefixes)


class MultiUserAuthMiddleware(BaseHTTPMiddleware):
    """Auth middleware for multi-user mode.

    Three decision paths:
    1. Public auth paths (setup, register, invite, status) → always pass
    2. Loopback, no proxy headers → pass (local-first UX) — but set
       user identity from JWT if present
    3. Proxied / non-loopback → require valid JWT session

    The middleware sets request.state.user_id and request.state.user_role
    when a valid JWT is present, regardless of path.
    """

    def __init__(self, app, auth_manager: AuthManager, user_store: "UserStore") -> None:
        super().__init__(app)
        self._auth_manager = auth_manager
        self._user_store = user_store

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        client_host = request.client.host if request.client else ""
        is_loopback = client_host in ("127.0.0.1", "::1", "localhost")
        proxied = _is_proxied(request)

        token = self._auth_manager.extract_token(request)
        payload = self._auth_manager.verify_token(token) if token else None

        if payload:
            user_id = payload.get("sub")
            request.state.user_id = user_id
            request.state.user_role = payload.get("role")
            set_user_home(_ROOT / "users" / user_id)
        else:
            request.state.user_id = None
            request.state.user_role = None
            set_user_home(None)

        user_status = payload.get("status") if payload else None
        if user_status == "pending" and path not in _PENDING_ALLOWED and not _is_public_auth_path(path):
            return JSONResponse(
                {"detail": "account_pending"},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )

        if _is_public_auth_path(path):
            return await call_next(request)

        if path in {"/health"}:
            return await call_next(request)

        if is_loopback and not proxied:
            return await call_next(request)

        if not proxied and not is_loopback:
            access_token = os.environ.get("NEXUS_ACCESS_TOKEN", "")
            if access_token:
                provided = token or ""
                if not provided:
                    provided = request.query_params.get("token", "")
                if provided != access_token:
                    return JSONResponse(
                        {"detail": "unauthorized"},
                        status_code=401,
                        headers={"Cache-Control": "no-store"},
                    )
            return await call_next(request)

        if not payload:
            if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
                r = RedirectResponse("/", status_code=307)
                r.headers["Cache-Control"] = "no-store"
                return r
            return JSONResponse(
                {"detail": "unauthorized"},
                status_code=401,
                headers={"Cache-Control": "no-store"},
            )

        # Role-based write gating: viewers cannot mutate server state.
        role = payload.get("role") if payload else None
        if role == "viewer" and request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if _is_api_path(path):
                return JSONResponse(
                    {"detail": "viewers cannot modify resources"},
                    status_code=403,
                    headers={"Cache-Control": "no-store"},
                )

        return await call_next(request)
