"""Routes for the optional Nexus account integration.

All endpoints are loopback-only — same posture as the tunnel admin
routes — so even a tunnel-authenticated client can't enumerate or rotate
the user's apiKey through them.

* ``POST /auth/nexus/verify``   — exchange a Firebase idToken for an apiKey.
  The website reconciles the LiteLLM key with the Firestore tier on every
  call, so re-signing-in after an upgrade/downgrade is sufficient to sync.
* ``GET  /auth/nexus/status``   — read the cached status (no outbound call).
* ``POST /auth/nexus/refresh``  — force a fresh /api/status fetch.
* ``POST /auth/nexus/logout``   — drop the apiKey + cached account record.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ...auth import nexus_account
from ...auth.status_watcher import StatusWatcher
from ..deps import get_app_state

router = APIRouter()
log = logging.getLogger(__name__)


def _require_loopback(request: Request) -> None:
    """403 if the request didn't come from the local machine.

    Mirrors the helper in ``routes/tunnel.py`` — kept duplicate (rather
    than imported) so the tunnel module isn't a hard dependency of every
    auth route.
    """
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="Nexus auth is loopback-only")
    if request.headers.get("x-forwarded-for") or request.headers.get("x-forwarded-host"):
        raise HTTPException(status_code=403, detail="Nexus auth is loopback-only")


def _watcher(request: Request) -> StatusWatcher | None:
    return getattr(request.app.state, "nexus_status_watcher", None)


def _account_view(*, watcher: StatusWatcher | None) -> dict[str, Any]:
    """Build the JSON shape returned by /status and /refresh.

    Never includes the apiKey. Reads from ``account.json`` for stable
    fields (email/tier) and from the watcher's in-memory cache for the
    live spend/budget snapshot.
    """
    record = nexus_account.load_account()
    signed_in = nexus_account.is_signed_in()
    last_status = watcher.last_status if watcher is not None else None
    out: dict[str, Any] = {
        "signedIn": signed_in,
        "email": (record or {}).get("email", "") if signed_in else "",
        "tier": (record or {}).get("tier", "free") if signed_in else "free",
        "cancelsAt": (record or {}).get("cancelsAt") or None,
        # The desktop has "connected" the apiKey when /api/keys/confirm
        # succeeded after sign-in. The UI surfaces this as a Connect CTA
        # when false (e.g. user signed in before the confirm path
        # existed, or confirm returned a transient error).
        "connected": bool((record or {}).get("connected", False)) if signed_in else False,
        "models": (record or {}).get("models") or [],
        "refreshedAt": (record or {}).get("refreshedAt", ""),
    }
    if last_status:
        out["status"] = last_status
    return out


@router.post("/auth/nexus/verify")
async def auth_nexus_verify(
    body: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    _require_loopback(request)
    cfg = get_app_state(request).get("cfg")
    base_url = (
        getattr(getattr(cfg, "nexus_account", None), "base_url", None)
        or "https://www.nexus-model.us"
    )
    id_token = body.get("idToken") or body.get("id_token") or ""
    try:
        record = await nexus_account.verify_id_token(id_token, base_url=base_url)
    except nexus_account.NexusAccountError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    # Kick the watcher right away so the gauges + tier reflect the fresh
    # /api/status payload without waiting for the next poll tick.
    watcher = _watcher(request)
    if watcher is not None:
        try:
            await watcher.tick_once()
        except nexus_account.NexusAccountError:
            log.warning("[nexus_account] post-verify status fetch failed")
    return record


@router.get("/auth/nexus/status")
async def auth_nexus_status(request: Request) -> dict[str, Any]:
    _require_loopback(request)
    return _account_view(watcher=_watcher(request))


@router.post("/auth/nexus/refresh")
async def auth_nexus_refresh(request: Request) -> dict[str, Any]:
    _require_loopback(request)
    if not nexus_account.is_signed_in():
        raise HTTPException(status_code=401, detail="not signed in")
    watcher = _watcher(request)
    if watcher is None:
        raise HTTPException(status_code=503, detail="status watcher not running")
    try:
        await watcher.tick_once()
    except nexus_account.NexusAccountError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return _account_view(watcher=watcher)


@router.post("/auth/nexus/logout")
async def auth_nexus_logout(request: Request) -> dict[str, Any]:
    _require_loopback(request)
    nexus_account.clear_account()
    # Reset the watcher's cached state so the UI immediately reflects sign-out.
    watcher = _watcher(request)
    if watcher is not None:
        watcher._last_status = None  # type: ignore[attr-defined]
        watcher._last_models = ()  # type: ignore[attr-defined]
    return {"signedIn": False}
