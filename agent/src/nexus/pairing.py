"""Native-client pairing — persistent bearer token + machine identity.

The native iOS client (and any API client on the tailnet) authenticates with
a persistent token minted here: ``~/.nexus/client_token`` (urlsafe 32-byte,
0600). This complements the two existing credential flows:

* **Tunnel access code** (ephemeral, per-activation, cookie-based) — for
  sharing over the public internet through a tunnel.
* **``NEXUS_ACCESS_TOKEN``** (env-only, set by the packaged launcher) — the
  back-compat path for non-loopback binds.

Pairing is for *owned devices on the tailnet*: Tailscale provides transport
encryption (WireGuard) and network-layer admission; the token provides
authentication and revocation (``nexus pair --rotate``). No app-layer
encryption key is needed.

Security invariants:

* The token is stored with 0600 perms next to the other nexus state; the
  ``.nexus`` dir is 0700.
* The token is only ever shown over **loopback** routes (``GET /pair``) or
  the terminal — never through the tunnel, never in a response to a
  non-loopback client.
* Rotation is loopback-only, takes effect immediately (mtime-checked lazy
  reload), and invalidates every previously paired device.
"""

from __future__ import annotations

import logging
import os
import platform
import secrets
import threading
from pathlib import Path
from urllib.parse import urlencode

log = logging.getLogger(__name__)

HOME_DIR = Path.home() / ".nexus"
TOKEN_PATH = HOME_DIR / "client_token"

DEEP_LINK_SCHEME = "nexus"


# ── token store ──────────────────────────────────────────────────────────────


def _write_token(token: str) -> None:
    HOME_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    TOKEN_PATH.write_text(token + "\n")
    try:
        os.chmod(TOKEN_PATH, 0o600)
    except OSError:  # pragma: no cover — Windows
        pass


def load_token() -> str | None:
    """Return the persisted client token, or None when never minted."""
    try:
        value = TOKEN_PATH.read_text().strip()
    except OSError:
        return None
    return value or None


def get_or_create_token() -> str:
    """Load the client token, minting it on first use (idempotent)."""
    token = load_token()
    if token is None:
        token = secrets.token_urlsafe(32)
        _write_token(token)
        log.info("client pairing token minted at %s", TOKEN_PATH)
    return token


def rotate_token() -> str:
    """Remint the client token — revokes every previously paired device."""
    token = secrets.token_urlsafe(32)
    _write_token(token)
    log.info("client pairing token rotated")
    return token


class ClientTokenStore:
    """Lazy, rotation-aware view of the client token for the auth middleware.

    Reads are cached and revalidated on the token file's mtime, so a rotate
    (or a first mint after boot) applies to in-flight server processes
    without a restart — the middleware is constructed once at app build,
    before the token necessarily exists.
    """

    def __init__(self, path: Path | None = None) -> None:
        # path=None defers to the module-global TOKEN_PATH on every read, so
        # tests (and anything else redirecting the store) are honored without
        # rebuilding the middleware stack.
        self._path = path
        self._lock = threading.Lock()
        self._token: str | None = None
        self._mtime: float | None = None

    def _resolve_path(self) -> Path:
        return self._path if self._path is not None else TOKEN_PATH

    def current(self) -> str | None:
        token_path = self._resolve_path()
        try:
            stat = token_path.stat()
        except OSError:
            with self._lock:
                self._token, self._mtime = None, None
            return None
        with self._lock:
            if self._token is not None and self._mtime == stat.st_mtime:
                return self._token
        token = load_token()
        with self._lock:
            self._token, self._mtime = token, stat.st_mtime
        return token


# ── machine identity ─────────────────────────────────────────────────────────


def machine_name(configured: str = "") -> str:
    """Display name for this Nexus instance (QR + /status handshake).

    Preference: the ``[server].machine_name`` config value, then the OS
    hostname (``platform.node()``), then "Nexus".
    """
    configured = (configured or "").strip()
    if configured:
        return configured
    host = platform.node().strip()
    if host:
        # Trim common domain suffixes: "Ninos-MacBook-Pro.local" →
        # "Ninos-MacBook-Pro" — friendlier in a pairing confirm sheet.
        return host.removesuffix(".local").removesuffix(".lan")
    return "Nexus"


# ── pairing payload / deep link ──────────────────────────────────────────────


def deep_link(*, name: str, host: str, port: int, token: str, tls: bool = False) -> str:
    """URL-encoded ``nexus://pair?...`` link — what the QR code encodes.

    Scanning it with the iOS camera opens the app straight into the pairing
    confirm screen. The token rides in the URL *of a QR image*, never of a
    network request. ``tls=1`` marks an https endpoint (tailscale serve).
    """
    params: dict[str, str] = {"n": name, "h": host, "p": str(port), "t": token}
    if tls:
        params["tls"] = "1"
    return f"{DEEP_LINK_SCHEME}://pair?" + urlencode(params)


def pairing_payload(
    *,
    host: str,
    port: int,
    name: str,
    token: str,
    features: dict[str, bool] | None = None,
    tls: bool = False,
) -> dict[str, object]:
    """The full pairing descriptor served by ``GET /pair`` (loopback-only)."""
    scheme = "https" if tls else "http"
    # 443 is implicit for https serve endpoints.
    suffix = "" if (tls and port == 443) else f":{port}"
    return {
        "v": 1,
        "name": name,
        "host": host,
        "port": port,
        "tls": tls,
        "url": f"{scheme}://{host}{suffix}",
        "token": token,
        "deep_link": deep_link(name=name, host=host, port=port, token=token, tls=tls),
        "features": features or {},
    }
