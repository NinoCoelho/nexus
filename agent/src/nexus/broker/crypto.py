from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


_BROKER_KEY_DIR = Path.home() / ".nexus" / "broker_keys"
_PRIVATE_KEY_PATH = _BROKER_KEY_DIR / "rsa_private.pem"


def generate_rsa_keypair() -> tuple[str, str]:
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return pub_pem, priv_pem


def rsa_decrypt(
    private_key_pem: str,
    encrypted_key_b64: str,
    encrypted_body_b64: str,
    iv_b64: str,
    tag_b64: str,
) -> str:
    private_key = serialization.load_pem_private_key(
        private_key_pem.encode(), password=None,
    )
    aes_key = private_key.decrypt(
        base64.b64decode(encrypted_key_b64),
        asym_padding.OAEP(
            mgf=asym_padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    nonce = base64.b64decode(iv_b64)
    ciphertext = base64.b64decode(encrypted_body_b64)
    tag = base64.b64decode(tag_b64)
    aesgcm = AESGCM(aes_key)
    plaintext = aesgcm.decrypt(nonce, ciphertext + tag, None)
    return plaintext.decode("utf-8")


def load_or_generate_private_key() -> tuple[str, str]:
    if _PRIVATE_KEY_PATH.exists():
        priv_pem = _PRIVATE_KEY_PATH.read_text()
        private_key = serialization.load_pem_private_key(
            priv_pem.encode(), password=None,
        )
        pub_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        return pub_pem, priv_pem

    pub_pem, priv_pem = generate_rsa_keypair()
    _BROKER_KEY_DIR.mkdir(parents=True, exist_ok=True)
    _PRIVATE_KEY_PATH.write_text(priv_pem)
    os.chmod(_PRIVATE_KEY_PATH, 0o600)
    return pub_pem, priv_pem
