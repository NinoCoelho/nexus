"""ACP device identity — ed25519 keypair management."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

DEVICE_KEY_PATH = Path("~/.nexus/device_key").expanduser()


@dataclass
class DeviceKeypair:
    private_key: Ed25519PrivateKey
    public_hex: str


def load_or_create_keypair(path: Path = DEVICE_KEY_PATH) -> DeviceKeypair:
    """Load an existing ed25519 keypair from *path* or generate and persist a new one.

    The private key is stored as a PEM file with mode 0600. The file is written
    atomically via a sibling ``.tmp`` file to avoid corruption on partial writes.
    """
    if path.exists():
        pem = path.read_bytes()
        private_key = serialization.load_pem_private_key(pem, password=None)
        if not isinstance(private_key, Ed25519PrivateKey):
            raise TypeError(f"Expected Ed25519 key in {path}, got {type(private_key).__name__}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        private_key = Ed25519PrivateKey.generate()
        pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(pem)
        os.chmod(tmp, 0o600)
        tmp.rename(path)

    pub_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return DeviceKeypair(private_key=private_key, public_hex=pub_bytes.hex())


def sign_challenge(
    keypair: DeviceKeypair,
    nonce: str,
    encoding: str = "hex",
) -> str:
    """Sign a nonce string with the device private key.

    Args:
        keypair: The loaded device keypair.
        nonce: The challenge nonce string (UTF-8 encoded before signing).
        encoding: ``"hex"`` (default) or ``"base64"``.

    Returns:
        The signature encoded as requested.
    """
    sig_bytes = keypair.private_key.sign(nonce.encode("utf-8"))
    if encoding == "base64":
        return base64.b64encode(sig_bytes).decode("ascii")
    return sig_bytes.hex()
