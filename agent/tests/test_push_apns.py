"""APNs push channel: registration routes, store round-trip, sender units.

No network: delivery goes through a stubbed ``_send_sync``, and the ES256
provider-token path is exercised against a locally generated key (the .p8
Apple issues is the same format — a PEM-wrapped DER EC key).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ── config plumbing ──────────────────────────────────────────────────────────


def test_is_configured_requires_all_fields(tmp_path: Path) -> None:
    from nexus.push import apns

    key = _make_key_file(tmp_path)
    full = _settings(key_path=str(key))
    assert apns.is_configured() is False  # nothing set in the real config

    # Full settings → configured (monkeypatch the loader).
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(apns, "_apns_settings", lambda: full)
        assert apns.is_configured() is True
        missing_key = _settings(key_path="")
        mp.setattr(apns, "_apns_settings", lambda: missing_key)
        assert apns.is_configured() is False


def _make_key_file(tmp_path: Path) -> Path:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path = tmp_path / "AuthKey_TEST.p8"
    path.write_bytes(pem)
    return path


def _settings(**overrides: str) -> object:
    from nexus.config_schema import ApnsSettings

    base = {
        "key_path": "/keys/AuthKey.p8",
        "key_id": "A1B2C3D4E5",
        "team_id": "TEAM12345",
        "bundle_id": "com.nexus.app",
        "environment": "production",
    }
    base.update(overrides)
    return ApnsSettings(**base)


def test_provider_token_is_valid_es256_jwt(tmp_path: Path) -> None:
    from nexus.push import apns

    key_file = _make_key_file(tmp_path)
    settings = _settings(key_path=str(key_file))
    apns._jwt_cache.token = None
    apns._jwt_cache.issued_at = 0.0
    token = apns._provider_token(settings)

    parts = token.split(".")
    assert len(parts) == 3

    def _dec(part: str) -> bytes:
        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))

    header = json.loads(_dec(parts[0]))
    claims = json.loads(_dec(parts[1]))
    assert header["alg"] == "ES256"
    assert header["kid"] == "A1B2C3D4E5"
    assert claims["iss"] == "TEAM12345"
    assert claims["iat"] > 0

    # Signature is the raw 64-byte r||s form, verifiable against the key.
    sig = _dec(parts[2])
    assert len(sig) == 64

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    priv = load_pem_private_key(key_file.read_bytes(), password=None)
    pub = priv.public_key()
    # Rebuild the DER signature from raw r||s for verification.
    r = int.from_bytes(sig[:32], "big")
    s = int.from_bytes(sig[32:], "big")
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    pub.verify(
        encode_dss_signature(r, s),
        f"{parts[0]}.{parts[1]}".encode(),
        ec.ECDSA(hashes.SHA256()),
    )
    apns._jwt_cache.token = None


# ── fan-out + pruning (no network) ───────────────────────────────────────────


class _FakeStore:
    def __init__(self, devices: list[dict]) -> None:
        self.devices = devices
        self.deleted: list[str] = []

    def list_apns_devices(self) -> list[dict]:
        return self.devices

    def delete_apns_device(self, token: str) -> bool:
        self.deleted.append(token)
        self.devices = [d for d in self.devices if d["token"] != token]
        return True


@pytest.mark.asyncio
async def test_fan_out_prunes_dead_devices(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from nexus.push import apns

    settings = _settings(key_path=str(_make_key_file(tmp_path)))
    monkeypatch.setattr(apns, "_apns_settings", lambda: settings)
    sent: list[dict] = []

    def _fake_send_sync(*, device, payload, settings, collapse_id):
        sent.append({"device": device, "payload": payload, "collapse": collapse_id})
        if device["token"].startswith("dead"):
            raise apns._DeviceGone()

    monkeypatch.setattr(apns, "_send_sync", _fake_send_sync)

    store = _FakeStore(
        [
            {"token": "a" * 64, "bundle_id": None, "environment": "production"},
            {"token": "dead" + "b" * 60, "bundle_id": None, "environment": "production"},
        ]
    )
    await apns.fan_out_apns(
        store=store,  # type: ignore[arg-type]
        session_id="sess-1",
        request_data={"prompt": "Approve this?", "request_id": "req-9", "kind": "confirm"},
    )

    assert len(sent) == 2
    assert store.deleted == ["dead" + "b" * 60]
    # Notification payload shape: alert + ids only, thread-id groups per chat.
    p = sent[0]["payload"]
    assert p["aps"]["alert"]["title"] == "Nexus needs input"
    assert p["aps"]["alert"]["body"] == "Approve this?"
    assert p["aps"]["thread-id"] == "sess-1"
    assert p["session_id"] == "sess-1"
    assert p["request_id"] == "req-9"
    assert sent[0]["collapse"] == "req-9"


@pytest.mark.asyncio
async def test_fan_out_noop_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexus.push import apns

    monkeypatch.setattr(apns, "_apns_settings", lambda: _settings(key_path=""))
    store = _FakeStore([{"token": "a" * 64, "bundle_id": None, "environment": "production"}])
    await apns.fan_out_apns(
        store=store,  # type: ignore[arg-type]
        session_id="s",
        request_data={"prompt": "x"},
    )
    assert store.devices  # untouched, nothing raised


# ── registration routes ──────────────────────────────────────────────────────


def _client() -> TestClient:
    from nexus.main import app

    return TestClient(app, client=("127.0.0.1", 12345))


def test_apns_register_round_trip() -> None:
    client = _client()
    token = "a1b2c3d4" * 8
    r = client.post(
        "/push/apns/register",
        json={"token": token, "bundle_id": "com.nexus.app", "environment": "sandbox"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["deliverable"], bool)

    # Re-register (upsert) must not 500 or duplicate.
    r2 = client.post("/push/apns/register", json={"token": token})
    assert r2.status_code == 200

    r3 = client.request("DELETE", "/push/apns/register", json={"token": token})
    assert r3.status_code == 200
    r4 = client.request("DELETE", "/push/apns/register", json={"token": token})
    assert r4.status_code == 404


def test_apns_register_validates_token() -> None:
    client = _client()
    r = client.post("/push/apns/register", json={"token": "short"})
    assert r.status_code == 422
    r2 = client.post("/push/apns/register", json={"token": "a" * 64, "environment": "dev"})
    assert r2.status_code == 422
