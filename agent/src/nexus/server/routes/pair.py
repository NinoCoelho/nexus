"""Pairing + status routes for the native client.

Three endpoints, three auth surfaces:

* ``GET  /pair`` — loopback-only. Mints the pairing token on first use and
  returns the full pairing descriptor (machine name, tailnet URL, token,
  deep link, feature flags) that the Settings UI / ``nexus pair`` render as
  a QR code. The token must never leave loopback through this route.
* ``POST /pair/rotate`` — loopback-only. Remints the token, revoking every
  previously paired device (lazy reload in the auth middleware — applies
  immediately, no restart).
* ``GET  /status`` — any authenticated client (loopback bypass, pairing
  token, or tunnel cookie). The native app's connect-time handshake:
  confirms the token is valid and reports machine identity + capabilities.
  No secrets in the response.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ... import __version__
from ...pairing import (
    get_or_create_token,
    machine_name,
    pairing_payload,
    rotate_token,
)
from ..bind import tailscale_ipv4

log = logging.getLogger(__name__)

router = APIRouter()

# Resolving the tailnet IP / serve host shells out — cache for the process.
# They only change when the machine re-joins a tailnet, and every consumer
# (pair QR, /status) treats them as best-effort display data.
_tailnet_ip_cache: str | None = None
_tailnet_ip_resolved = False
_serve_host_cache: str | None = None
_serve_host_resolved = False


def _cached_tailnet_ip() -> str | None:
    global _tailnet_ip_cache, _tailnet_ip_resolved
    if not _tailnet_ip_resolved:
        try:
            _tailnet_ip_cache = tailscale_ipv4()
        except (OSError, ValueError, RuntimeError):  # resolution is best-effort
            _tailnet_ip_cache = None
        _tailnet_ip_resolved = True
    return _tailnet_ip_cache


def _cached_serve_host(port: int) -> str | None:
    global _serve_host_cache, _serve_host_resolved
    if not _serve_host_resolved:
        try:
            from ..bind import tailscale_serve_host

            _serve_host_cache = tailscale_serve_host(port)
        except (OSError, ValueError, RuntimeError):
            _serve_host_cache = None
        _serve_host_resolved = True
    return _serve_host_cache


def _advertised_endpoint(port: int) -> tuple[str, int, bool]:
    """(host, port, tls) the QR should point the phone at.

    Preference: a tailnet-only `tailscale serve` HTTPS host (real cert —
    iOS 26's hardened ATS rejects cleartext even with exemptions for apps
    built on the new SDK), then the raw tailnet IPv4 over http.
    """
    serve_host = _cached_serve_host(port)
    if serve_host:
        return serve_host, 443, True
    return (_cached_tailnet_ip() or "127.0.0.1"), port, False


def _require_loopback(request: Request) -> None:
    """403 if the request didn't come from the local machine (mirrors tunnel admin)."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="Pairing admin is loopback-only")
    if request.headers.get("x-forwarded-for") or request.headers.get("x-forwarded-host"):
        raise HTTPException(status_code=403, detail="Pairing admin is loopback-only")


def _server_port(request: Request) -> int:
    """The port this server listens on, as best a route can know it."""
    host_header = request.headers.get("host", "")
    if host_header.startswith("["):  # [v6]:port
        if "]:" in host_header:
            tail = host_header.split("]:", 1)[1]
            if tail.isdigit():
                return int(tail)
    elif host_header.count(":") == 1:
        tail = host_header.rsplit(":", 1)[1]
        if tail.isdigit():
            return int(tail)
    import os

    raw = os.environ.get("NEXUS_PORT", "").strip()
    if raw.isdigit():
        return int(raw)
    return 18989


def _feature_flags(cfg: Any) -> dict[str, bool]:
    browser = getattr(cfg, "browser", None)
    from ...push import apns as push_apns

    return {
        "browser": bool(getattr(browser, "enabled", False)),
        "browser_remote_control": bool(getattr(browser, "remote_control", False)),
        "coordinator": bool(getattr(getattr(cfg, "coordinator", None), "enabled", False)),
        "telegram": bool(getattr(getattr(cfg, "telegram", None), "enabled", False)),
        "push_apns": push_apns.is_configured(),
    }


def _cfg(request: Request) -> Any:
    return request.app.state.mutable_state["cfg"]


@router.get("/pair")
async def pair(request: Request) -> dict[str, Any]:
    _require_loopback(request)
    cfg = _cfg(request)
    token = get_or_create_token()
    port = _server_port(request)
    host, adv_port, tls = _advertised_endpoint(port)
    name = machine_name(getattr(getattr(cfg, "server", None), "machine_name", "") or "")
    return pairing_payload(
        host=host,
        port=adv_port,
        name=name,
        token=token,
        features=_feature_flags(cfg),
        tls=tls,
    )


@router.post("/pair/rotate")
async def pair_rotate(request: Request) -> dict[str, Any]:
    _require_loopback(request)
    cfg = _cfg(request)
    token = rotate_token()
    port = _server_port(request)
    host, adv_port, tls = _advertised_endpoint(port)
    name = machine_name(getattr(getattr(cfg, "server", None), "machine_name", "") or "")
    return pairing_payload(
        host=host,
        port=adv_port,
        name=name,
        token=token,
        features=_feature_flags(cfg),
        tls=tls,
    )


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    cfg = _cfg(request)
    name = machine_name(getattr(getattr(cfg, "server", None), "machine_name", "") or "")
    return {
        "ok": True,
        "version": __version__,
        "machine_name": name,
        "tailnet_ip": _cached_tailnet_ip(),
        "port": _server_port(request),
        "features": _feature_flags(cfg),
    }
