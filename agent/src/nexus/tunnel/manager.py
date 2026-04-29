"""TunnelManager — process-wide singleton that owns the active tunnel + secrets.

State machine: inactive → active → inactive. Activation generates two secrets:

  * ``token``  — long random session credential (32 bytes urlsafe). Lives in an
                 ``HttpOnly Secure SameSite=Strict`` cookie after redemption.
                 Never appears in URLs.
  * ``code``   — short typeable code (8 base32 chars, formatted as ``XXXX-XXXX``).
                 What the user types on their phone after opening the URL.
                 Exchanged for the long token via ``POST /tunnel/redeem``.

Splitting the secret in two means the share URL itself carries no credential:
``https://abc-words.trycloudflare.com/`` is safe to put in browser history,
screenshots, QR codes, dashboard logs, etc. The code is the only thing that
needs to travel through a side channel (verbal, SMS, glance at the desktop).

State is **in-memory only**; restarting the daemon resets the tunnel to off.
The user's mental model is "I activated a session", not "permanent infrastructure".

Token / code comparisons use ``hmac.compare_digest`` to keep validation timing-safe.
"""

from __future__ import annotations

import hmac
import logging
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Literal

from . import cloudflared_provider

log = logging.getLogger(__name__)


Provider = Literal["cloudflare"]

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
    redeemed: bool         # True once a device has consumed the code; code is then nulled out


class TunnelManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: bool = False
        self._provider: Provider | None = None
        self._public_url: str | None = None
        self._token: str | None = None
        self._code: str | None = None
        self._started_at: float | None = None
        self._process: subprocess.Popen[bytes] | None = None
        # Per-activation nonce baked into share_url. Forces a fresh HTTP cache
        # key on the redeemer's device every time, so phones that hold a stale
        # cached response from a previous session (iOS Safari is especially
        # aggressive here) hit a clean URL and reload from network.
        self._share_nonce: str | None = None
        # Single-use latch: flipped on the first successful consume_code() and
        # reset on stop()/start(). While set, the code is hidden from status
        # responses and further redemption attempts are rejected.
        self._redeemed: bool = False

    # ── lifecycle ─────────────────────────────────────────────────────────

    def start(
        self,
        *,
        port: int,
        provider: Provider = "cloudflare",
    ) -> TunnelStatus:
        with self._lock:
            if self._active:
                return self._status_locked()
            if provider != "cloudflare":
                raise ValueError(f"Unsupported tunnel provider: {provider}")

            token = secrets.token_urlsafe(32)
            code = _generate_code()
            nonce = secrets.token_urlsafe(6)
            proc, public_url = cloudflared_provider.start_tunnel(port=port)

            self._active = True
            self._provider = provider
            self._public_url = public_url
            self._token = token
            self._code = code
            self._started_at = time.time()
            self._process = proc
            self._share_nonce = nonce
            self._redeemed = False
            log.info("tunnel started: %s", public_url)
            return self._status_locked()

    def stop(self) -> TunnelStatus:
        with self._lock:
            if not self._active:
                return self._status_locked()
            cloudflared_provider.stop_tunnel(self._process)
            self._active = False
            self._provider = None
            self._public_url = None
            self._token = None
            self._code = None
            self._started_at = None
            self._process = None
            self._share_nonce = None
            self._redeemed = False
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

        Single-use per activation. The first successful redemption burns the
        code: the latch flips, ``status()`` stops echoing it, and further calls
        return ``None`` until ``stop()`` + ``start()`` mints a new one. The
        user's mental model is "I paired my phone, done"; reconnecting another
        device requires explicitly restarting sharing.
        """
        if not self._active or not self._code or not self._token:
            return None
        if self._redeemed:
            return None
        normalized = _normalize_code(candidate)
        expected = _normalize_code(self._code)
        if not normalized or len(normalized) != len(expected):
            return None
        if hmac.compare_digest(normalized, expected):
            self._redeemed = True
            return self._token
        return None

    # ── internals ─────────────────────────────────────────────────────────

    def _status_locked(self) -> TunnelStatus:
        share_url = None
        if self._active and self._public_url:
            # `?v=<nonce>` is a cache buster, not a credential. The query is
            # ignored by the SPA's React Router (which routes by pathname) and
            # by the auth middleware (which switches on path + headers). It
            # exists solely to make the device's HTTP cache treat each
            # activation's URL as new, even on a recycled subdomain.
            share_url = f"{self._public_url}/?v={self._share_nonce}"
        # Once redeemed, the code is burned: don't echo it back even to the
        # loopback admin caller. Anyone glancing at the desktop after pairing
        # shouldn't be able to read the code off-screen.
        code_out = None if self._redeemed else self._code
        return TunnelStatus(
            active=self._active,
            provider=self._provider,
            public_url=self._public_url,
            share_url=share_url,
            code=code_out,
            started_at=self._started_at,
            redeemed=self._redeemed,
        )


_singleton: TunnelManager | None = None


def get_manager() -> TunnelManager:
    """Process-wide singleton. The middleware and route handlers share this."""
    global _singleton
    if _singleton is None:
        _singleton = TunnelManager()
    return _singleton
