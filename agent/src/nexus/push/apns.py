"""APNs delivery for the native iOS app — token-based (.p8) provider.

Mirrors the Web Push channel (``sender.py``): a fan-out entry point fired
from ``PubSubMixin._schedule_push``, one HTTP call per registered device,
dead devices pruned on Apple's 410/400 verdicts. No extra dependencies —
``httpx`` for transport, ``cryptography`` (already required for the site
credentials Fernet store) to sign the ES256 provider JWT.

Apple specifics implemented here:

* **Provider token** — ES256 JWT (``{alg: ES256, kid}`` header, ``{iss:
  team_id, iat}`` claims), cached and refreshed at 45 min (Apple's hard
  limit is 1 h). ECDSA signatures from ``cryptography`` are ASN.1 DER;
  APNs wants the raw ``r||s`` (64 bytes) form, so they're re-encoded.
* **Topics** — ``apns-topic`` = bundle_id. Debug builds run under the
  sandbox environment, selected per device row.
* **Payload** — notification-only: title/body + opaque ids. Content stays
  on the server; the app fetches state on tap.

Config (``[push].apns`` in ``~/.nexus/config.toml``): ``key_path``,
``key_id``, ``team_id``, ``bundle_id``, ``environment``. Any missing field
disables the channel — ``is_configured()`` is the cheap gate.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import TYPE_CHECKING, Any

from ..config_file import load as _load_config

if TYPE_CHECKING:
    from ..server.session_store import SessionStore

log = logging.getLogger(__name__)

_HOSTS = {
    "production": "https://api.push.apple.com",
    "sandbox": "https://api.sandbox.push.apple.com",
}

# Provider-token cache. Apple invalidates tokens older than 1 h; refresh
# comfortably inside that.
_JWT_TTL_SECONDS = 45 * 60


class ApnsNotConfigured(RuntimeError):
    """The [push].apns section is incomplete — channel inactive."""


class _JwtCache:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.token: str | None = None
        self.issued_at: float = 0.0


_jwt_cache = _JwtCache()


def _apns_settings() -> Any:
    return _load_config().push.apns


def is_configured() -> bool:
    """Cheap check before scheduling delivery — no key load, no network."""
    try:
        s = _apns_settings()
    except Exception:  # noqa: BLE001 — config unreadable = not configured
        return False
    return bool(s.key_path and s.key_id and s.team_id and s.bundle_id)


def _b64url(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _provider_token(settings: Any) -> str:
    """ES256 JWT, cached per process for 45 minutes."""
    with _jwt_cache.lock:
        if _jwt_cache.token and (time.time() - _jwt_cache.issued_at) < _JWT_TTL_SECONDS:
            return _jwt_cache.token

    import json as _json

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import (
        decode_dss_signature,
    )

    # Apple .p8 auth keys are PEM (PKCS#8 "BEGIN PRIVATE KEY", sometimes
    # "BEGIN EC PRIVATE KEY") — load_pem_private_key handles both.
    key = serialization.load_pem_private_key(
        open(settings.key_path, "rb").read(),  # noqa: SIM115 — short-lived read
        password=None,
    )
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ApnsNotConfigured("APNs auth key is not an EC key")
    header = _b64url(_json.dumps({"alg": "ES256", "kid": settings.key_id}).encode())
    claims = _b64url(
        _json.dumps({"iss": settings.team_id, "iat": int(time.time())}).encode()
    )
    signing_input = f"{header}.{claims}".encode()
    der_sig = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der_sig)
    raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    token = f"{header}.{claims}.{_b64url(raw_sig)}"

    with _jwt_cache.lock:
        _jwt_cache.token = token
        _jwt_cache.issued_at = time.time()
    return token


class _DeviceGone(Exception):
    """Apple says the token is dead (410/400 BadDeviceToken) — prune it."""


def _send_sync(
    *,
    device: dict[str, Any],
    payload: dict[str, Any],
    settings: Any,
    collapse_id: str | None,
) -> None:
    """Blocking APNs POST — invoked via asyncio.to_thread."""
    import httpx

    env = (device.get("environment") or settings.environment or "production").lower()
    host = _HOSTS.get(env, _HOSTS["production"])
    token = device["token"]
    url = f"{host}/3/device/{token}"

    headers = {
        "authorization": f"Bearer {_provider_token(settings)}",
        "apns-topic": settings.bundle_id,
        "apns-push-type": "alert",
        "apns-priority": "10",
        "content-type": "application/json",
    }
    if collapse_id:
        headers["apns-collapse-id"] = collapse_id[:64]

    with httpx.Client(http2=False, timeout=10.0) as client:
        resp = client.post(url, headers=headers, content=json.dumps(payload))
    if resp.status_code == 200:
        return
    reason = ""
    try:
        reason = resp.json().get("reason", "")
    except (ValueError, AttributeError):
        pass  # body may be empty/non-JSON on transport errors
    if resp.status_code == 410 or reason in ("BadDeviceToken", "Unregistered"):
        raise _DeviceGone()
    raise RuntimeError(f"APNs {resp.status_code} {reason or resp.text[:120]}")


async def fan_out_apns(
    *,
    store: SessionStore,
    session_id: str,
    request_data: dict[str, Any],
) -> None:
    """Push the HITL prompt to every registered iOS device."""
    if not is_configured():
        return
    devices = store.list_apns_devices()
    if not devices:
        return

    settings = _apns_settings()
    body = (request_data.get("prompt") or "").strip()
    if len(body) > 240:
        body = body[:237] + "…"
    payload = {
        "aps": {
            "alert": {"title": "Nexus needs input", "body": body},
            "sound": "default",
            "thread-id": session_id[:64],
        },
        "session_id": session_id,
        "request_id": request_data.get("request_id"),
        "kind": request_data.get("kind"),
    }
    collapse = str(request_data.get("request_id") or "")[:64] or None

    async def _one(device: dict[str, Any]) -> None:
        try:
            await asyncio.to_thread(
                _send_sync,
                device=device,
                payload=payload,
                settings=settings,
                collapse_id=collapse,
            )
        except _DeviceGone:
            log.info("pruning expired APNs device %s…", device["token"][:12])
            store.delete_apns_device(device["token"])
        except Exception:  # one device failing must not block others
            log.warning("APNs delivery failed for %s…", device["token"][:12], exc_info=True)

    await asyncio.gather(*[_one(d) for d in devices], return_exceptions=True)
