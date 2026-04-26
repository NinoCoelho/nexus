"""VAPID keypair management.

VAPID (Voluntary Application Server Identification) keys identify the
backend to browser push services. They're a fixed P-256 keypair, the
public key is shared with the browser at subscription time, and the
private key signs each push request.

Stored at ``~/.nexus/push.json``. Auto-generated on first read so a
fresh install Just Works. The subject (``mailto:`` or origin URL) is
required by the spec for push services to contact the operator.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

log = logging.getLogger(__name__)

_KEYS_PATH = Path("~/.nexus/push.json").expanduser()
_DEFAULT_SUBJECT = "mailto:admin@nexus.local"


@dataclass(frozen=True)
class VapidKeys:
    public_key: str   # urlsafe-base64 raw uncompressed point (88 chars)
    private_key: str  # urlsafe-base64 PEM (used by pywebpush)
    subject: str


def _b64url_nopad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _generate() -> VapidKeys:
    """Generate a fresh P-256 keypair in pywebpush-friendly form."""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key()

    # Public: raw uncompressed point (0x04 || X || Y), urlsafe-b64 nopad —
    # this is the format the SubscribeOptions.applicationServerKey expects
    # after the browser turns it back into a Uint8Array.
    raw_point = pub.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public_b64 = _b64url_nopad(raw_point)

    # Private: pywebpush accepts the raw 32-byte d value as urlsafe-b64
    # nopad, which is what the official VAPID examples use. We store
    # exactly that — no PEM wrapping required.
    priv_numbers = priv.private_numbers()
    d_bytes = priv_numbers.private_value.to_bytes(32, "big")
    private_b64 = _b64url_nopad(d_bytes)

    return VapidKeys(
        public_key=public_b64,
        private_key=private_b64,
        subject=_DEFAULT_SUBJECT,
    )


def load_or_create(subject: str | None = None) -> VapidKeys:
    """Return the persisted keypair, generating one on first call."""
    if _KEYS_PATH.exists():
        try:
            data = json.loads(_KEYS_PATH.read_text())
            return VapidKeys(
                public_key=data["public_key"],
                private_key=data["private_key"],
                subject=data.get("subject") or subject or _DEFAULT_SUBJECT,
            )
        except (KeyError, ValueError, OSError):
            log.warning("push.json malformed — regenerating", exc_info=True)

    keys = _generate()
    if subject:
        keys = VapidKeys(
            public_key=keys.public_key,
            private_key=keys.private_key,
            subject=subject,
        )
    _KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _KEYS_PATH.write_text(json.dumps({
        "public_key": keys.public_key,
        "private_key": keys.private_key,
        "subject": keys.subject,
    }, indent=2))
    try:
        os.chmod(_KEYS_PATH, 0o600)
    except OSError:
        pass
    log.info("Generated new VAPID keypair at %s", _KEYS_PATH)
    return keys
