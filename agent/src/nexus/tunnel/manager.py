"""TunnelManager — process-wide singleton that owns the active tunnel + secrets.

State machine: inactive → active → inactive. Activation generates two secrets:

  * ``token``  — long random session credential (32 bytes urlsafe). Lives in an
                 ``HttpOnly Secure SameSite=Strict`` cookie after redemption.
                 Never appears in URLs.
  * ``code``   — short typeable code (8 base32 chars, formatted as ``XXXX-XXXX``).
                 What the user types on their phone after opening the URL.
                 Exchanged for the long token via ``POST /tunnel/redeem``.

Splitting the secret in two means the share URL itself carries no credential:
``https://abc.ngrok-free.app/`` is safe to put in browser history, screenshots,
QR codes, ngrok dashboard logs, etc. The code is the only thing that needs to
travel through a side channel (verbal, SMS, glance at the desktop).

State is **in-memory only**; restarting the daemon resets the tunnel to off.
The user's mental model is "I activated a session", not "permanent infrastructure".

Token / code comparisons use ``hmac.compare_digest`` to keep validation timing-safe.
"""

from __future__ import annotations

import hmac
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Literal

from . import ngrok_provider

log = logging.getLogger(__name__)


Provider = Literal["ngrok"]

# Crockford-ish base32 minus easy-to-confuse characters (0/O, 1/I/L). Avoiding
# vowels would also drop accidental words but isn't required for security —
# 32^8 ≈ 1.1e12 means brute force isn't a threat as long as we rate-limit.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LEN = 8


def _generate_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN))
    return f"{raw[:4]}-{raw[4:]}"


def _normalize_code(candidate: str | None) -> str:
    """Strip whitespace + dashes, uppercase. Lets users paste/type with formatting."""
    if not candidate:
        return ""
    return "".join(ch for ch in candidate.upper() if ch in _CODE_ALPHABET)


@dataclass
class TunnelStatus:
    active: bool
    provider: Provider | None
    public_url: str | None
    share_url: str | None  # plain URL, no secret. Phone navigates here.
    code: str | None       # short code for the login form. Loopback-only display.
    started_at: float | None  # unix epoch seconds


class TunnelManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: bool = False
        self._provider: Provider | None = None
        self._public_url: str | None = None
        self._token: str | None = None
        self._code: str | None = None
        self._started_at: float | None = None

    # ── lifecycle ─────────────────────────────────────────────────────────

    def start(
        self,
        *,
        port: int,
        provider: Provider = "ngrok",
        authtoken: str = "",
        region: str = "us",
    ) -> TunnelStatus:
        with self._lock:
            if self._active:
                return self._status_locked()
            if provider != "ngrok":
                raise ValueError(f"Unsupported tunnel provider: {provider}")

            token = secrets.token_urlsafe(32)
            code = _generate_code()
            try:
                public_url = ngrok_provider.start_ngrok(
                    authtoken=authtoken, port=port, region=region,
                )
            except ngrok_provider.NgrokError:
                raise

            self._active = True
            self._provider = provider
            self._public_url = public_url
            self._token = token
            self._code = code
            self._started_at = time.time()
            log.info("tunnel started: %s", public_url)
            return self._status_locked()

    def stop(self) -> TunnelStatus:
        with self._lock:
            if not self._active:
                return self._status_locked()
            if self._provider == "ngrok":
                ngrok_provider.stop_ngrok()
            self._active = False
            self._provider = None
            self._public_url = None
            self._token = None
            self._code = None
            self._started_at = None
            log.info("tunnel stopped")
            return self._status_locked()

    def status(self) -> TunnelStatus:
        with self._lock:
            return self._status_locked()

    # ── auth helpers (called from middleware on every request) ────────────

    def is_active(self) -> bool:
        return self._active

    def validate_token(self, candidate: str | None) -> bool:
        """Validate the long session token (cookie carrier). Timing-safe."""
        if not self._active or not self._token or not candidate:
            return False
        return hmac.compare_digest(candidate, self._token)

    def consume_code(self, candidate: str | None) -> str | None:
        """Validate the short access code; on success return the long token to seat in a cookie.

        Multi-use until tunnel stop, deliberately — the user's mental model is
        "I share the link with my phone and my tablet and a friend". Brute force
        is gated by ngrok's per-tunnel request volume + the alphabet size.
        """
        if not self._active or not self._code or not self._token:
            return None
        normalized = _normalize_code(candidate)
        expected = _normalize_code(self._code)
        if not normalized or len(normalized) != len(expected):
            return None
        if hmac.compare_digest(normalized, expected):
            return self._token
        return None

    # ── internals ─────────────────────────────────────────────────────────

    def _status_locked(self) -> TunnelStatus:
        return TunnelStatus(
            active=self._active,
            provider=self._provider,
            public_url=self._public_url,
            share_url=self._public_url + "/" if self._active and self._public_url else None,
            code=self._code,
            started_at=self._started_at,
        )


_singleton: TunnelManager | None = None


def get_manager() -> TunnelManager:
    """Process-wide singleton. The middleware and route handlers share this."""
    global _singleton
    if _singleton is None:
        _singleton = TunnelManager()
    return _singleton
