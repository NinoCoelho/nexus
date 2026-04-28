"""Routes for tunnel control: admin (start/stop/status) and the public redeem flow.

Three groups of endpoints, with different auth surfaces:

* **Admin** (``/tunnel/start|stop|status|install``) — loopback-only. The user's
  desktop UI calls these to manage the tunnel; never reachable from the tunnel
  itself, even with a valid cookie.
* **Public over tunnel** (``/tunnel/redeem``, ``/tunnel/auth-status``) — designed
  to be reachable from the proxied side without a cookie. ``redeem`` is how the
  phone exchanges its access code for a session cookie; ``auth-status`` lets the
  SPA decide whether to render the login screen or the app.
* **Loopback admin status** returns the short access code; the public-side
  status endpoint never does.

The middleware path policy in ``app.py`` complements this: it lets the public
endpoints through without a cookie, and gates everything else.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from ...tunnel import get_manager

router = APIRouter()

# Cookie name must match the constant in app.py (LoopbackOrTokenMiddleware).
TUNNEL_COOKIE = "nexus_tunnel_token"

# ── /tunnel/redeem rate limiter ─────────────────────────────────────────────
# Defense-in-depth on top of the 32^8 (~1.1 trillion) code entropy. Caps
# attempts per source IP so an attacker can't run an online brute force at
# wire speed — they'd hit the wall at a few attempts per minute and the user
# would notice / can stop the tunnel.
#
# Window: 10 minutes; max 8 wrong attempts per IP. Successful redemptions
# don't count against the limit (otherwise legit users redeeming on multiple
# devices would burn through it).
_RATE_WINDOW_SEC = 600
_RATE_MAX_ATTEMPTS = 8
_rate_attempts: dict[str, deque[float]] = {}
_rate_lock = threading.Lock()


def _rate_limited(key: str) -> bool:
    now = time.monotonic()
    cutoff = now - _RATE_WINDOW_SEC
    with _rate_lock:
        bucket = _rate_attempts.setdefault(key, deque())
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        return len(bucket) >= _RATE_MAX_ATTEMPTS


def _record_failed_attempt(key: str) -> None:
    with _rate_lock:
        _rate_attempts.setdefault(key, deque()).append(time.monotonic())


def _request_is_proxied(request: Request) -> bool:
    """Did this request hop through the public tunnel?

    Mirror of ``server.app._is_proxied`` (kept inlined to avoid importing app.py
    from a routes module). Used by ``/tunnel/auth-status`` to tell the SPA whether
    it's running on the loopback owner's machine or on a remote redeemer's
    device — the UI hides admin-only surfaces in the latter case.
    """
    h = request.headers
    return bool(
        h.get("x-forwarded-for")
        or h.get("x-forwarded-host")
        or h.get("cf-ray")
        or h.get("cf-connecting-ip")
        or h.get("ngrok-trace-id")
    )


def _client_key(request: Request) -> str:
    """Best-available identifier for rate-limit bucketing.

    The tunnel forwards the original client IP in ``x-forwarded-for``; we
    honor it when present (otherwise every tunnel request looks like
    127.0.0.1 to us and an attacker could share a global bucket). The first
    IP in the list is the original client; intermediate hops follow.
    """
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _require_loopback(request: Request) -> None:
    """403 if the request didn't come from the local machine."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="Tunnel admin is loopback-only")
    # Even on loopback, reject if forwarded — the local client could itself be
    # a proxy from the public tunnel back into us.
    if request.headers.get("x-forwarded-for") or request.headers.get("x-forwarded-host"):
        raise HTTPException(status_code=403, detail="Tunnel admin is loopback-only")


def _server_port() -> int:
    """Pick the port the FastAPI server is listening on. Defaults to 18989."""
    raw = os.environ.get("NEXUS_PORT", "").strip()
    if raw.isdigit():
        return int(raw)
    return 18989


def _admin_status_dict() -> dict[str, Any]:
    """Full status, including the short code. Returned only to loopback callers."""
    from ...tunnel import cloudflared_provider
    s = get_manager().status()
    return {
        "active": s.active,
        "provider": s.provider,
        "public_url": s.public_url,
        "share_url": s.share_url,
        "code": s.code,  # short access code; never leave loopback with this
        "started_at": s.started_at,
        "binary_installed": cloudflared_provider.binary_installed(),
    }


# ── admin (loopback only) ───────────────────────────────────────────────────


@router.get("/tunnel/status")
async def tunnel_status(request: Request) -> dict[str, Any]:
    _require_loopback(request)
    return _admin_status_dict()


@router.post("/tunnel/start")
async def tunnel_start(request: Request) -> dict[str, Any]:
    _require_loopback(request)
    # The first activation triggers a ~30MB binary download inside start_tunnel.
    # Push the call onto a thread so the event loop stays responsive — without
    # this, SSE clients see the keepalive stream stall during install.
    import asyncio
    try:
        await asyncio.to_thread(
            get_manager().start,
            port=_server_port(), provider="cloudflare",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return _admin_status_dict()


@router.post("/tunnel/stop")
async def tunnel_stop(request: Request) -> dict[str, Any]:
    _require_loopback(request)
    get_manager().stop()
    return _admin_status_dict()


@router.post("/tunnel/install")
async def tunnel_install_binary(request: Request) -> dict[str, Any]:
    """Idempotently install the cloudflared binary.

    Loopback-only. Pre-flight step before activating sharing — lets the UI show
    a "downloading…" state explicitly instead of having the user wait silently
    inside ``POST /tunnel/start``. Safe to call when already installed (no-op).
    """
    _require_loopback(request)
    import asyncio
    from ...tunnel import cloudflared_provider
    try:
        path = await asyncio.to_thread(cloudflared_provider.install_binary)
    except cloudflared_provider.CloudflaredError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"ok": True, "path": str(path), "installed": True}


# ── public over tunnel (no cookie required) ────────────────────────────────


@router.post("/tunnel/redeem")
async def tunnel_redeem(request: Request, body: dict[str, Any]) -> Response:
    """Exchange a short access code for a long session cookie.

    The phone's SPA hits this with ``{code: "ABCD-EFGH"}`` after the user types
    the code shown on the desktop. Validation is timing-safe and case-insensitive
    (we normalize to upper, drop whitespace and dashes). Per-IP rate-limited so
    online brute force is throttled even though the entropy is already high.
    """
    bucket = _client_key(request)
    if _rate_limited(bucket):
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Wait a few minutes and try again.",
        )
    code = (body or {}).get("code", "")
    if not isinstance(code, str) or not code.strip():
        raise HTTPException(status_code=400, detail="code is required")
    long_token = get_manager().consume_code(code)
    if long_token is None:
        _record_failed_attempt(bucket)
        # 401 instead of 404 so brute-forcers see standard auth-failure noise.
        raise HTTPException(status_code=401, detail="invalid code")

    response = Response(content='{"ok":true}', media_type="application/json")
    response.set_cookie(
        key=TUNNEL_COOKIE,
        value=long_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=60 * 60 * 24 * 7,
        path="/",
    )
    return response


@router.get("/tunnel/auth-status")
async def tunnel_auth_status(request: Request) -> dict[str, Any]:
    """Tells the SPA whether to render the login form or boot normally.

    Reachable from the tunnel without a cookie precisely so the SPA can probe
    auth on first load. Never echoes any secret back — only a yes/no.
    """
    proxied = _request_is_proxied(request)
    mgr = get_manager()
    if not mgr.is_active():
        # No tunnel running. Either we're on loopback (and don't need auth) or
        # the request shouldn't have reached us at all — be permissive so dev /
        # local-network use keeps working.
        return {"requires_redeem": False, "tunnel_active": False, "proxied": proxied}

    cookie = request.cookies.get(TUNNEL_COOKIE, "")
    auth_header = request.headers.get("authorization", "")
    bearer = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
    has_cookie = mgr.validate_token(cookie) or mgr.validate_token(bearer)
    return {
        "requires_redeem": not has_cookie,
        "tunnel_active": True,
        "proxied": proxied,
    }
