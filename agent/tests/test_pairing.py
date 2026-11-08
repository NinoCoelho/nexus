"""Tests for native-client pairing: token store, bind resolution, /pair + /status.

Two layers:

* Unit — pairing token mint/rotate/lazy-reload, machine-name resolution,
  bind-spec resolution (tailscale mocked; no network, no real interfaces).
* Routes + middleware — through ``TestClient`` against the real app:
  ``GET /pair`` mints and returns the descriptor (loopback only), rotation
  revokes, ``/status`` is the token-authed handshake for non-loopback
  clients, and the middleware accepts the pairing token as a Bearer for
  direct tailnet clients (fail-closed without it).

The token file is redirected to a tmp dir for every test — never touch the
developer's real ``~/.nexus/client_token``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import nexus.pairing as pairing
from nexus.server import bind as netbind


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _tmp_token_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    home = tmp_path / ".nexus"
    monkeypatch.setattr(pairing, "HOME_DIR", home)
    monkeypatch.setattr(pairing, "TOKEN_PATH", home / "client_token")
    # Reset the route-level caches so each test controls them — never
    # depends on this dev machine's live tailscale state.
    from nexus.server.routes import pair as pair_route

    monkeypatch.setattr(pair_route, "_tailnet_ip_cache", "100.64.10.20")
    monkeypatch.setattr(pair_route, "_tailnet_ip_resolved", True)
    monkeypatch.setattr(pair_route, "_serve_host_cache", None)
    monkeypatch.setattr(pair_route, "_serve_host_resolved", True)
    yield


def _loopback_client() -> TestClient:
    from nexus.main import app

    return TestClient(app, client=("127.0.0.1", 12345))


def _tailnet_client() -> TestClient:
    from nexus.main import app

    return TestClient(app, client=("100.64.10.99", 54321))


# ── pairing unit tests ───────────────────────────────────────────────────────


def test_get_or_create_token_is_idempotent() -> None:
    first = pairing.get_or_create_token()
    assert len(first) >= 32
    assert pairing.load_token() == first
    assert pairing.get_or_create_token() == first


def test_token_file_permissions() -> None:
    pairing.get_or_create_token()
    mode = pairing.TOKEN_PATH.stat().st_mode & 0o777
    assert mode == 0o600 or os.name == "nt"


def test_rotate_token_revokes_old() -> None:
    old = pairing.get_or_create_token()
    new = pairing.rotate_token()
    assert new != old
    assert pairing.load_token() == new


def test_client_token_store_lazy_reload() -> None:
    store = pairing.ClientTokenStore(path=pairing.TOKEN_PATH)
    assert store.current() is None  # never minted

    token = pairing.get_or_create_token()
    assert store.current() == token

    # Rotate out-of-band with a distinct mtime → store must notice.
    pairing.rotate_token()
    stat = pairing.TOKEN_PATH.stat()
    os.utime(pairing.TOKEN_PATH, (stat.st_atime + 10, stat.st_mtime + 10))
    assert store.current() != token


def test_config_round_trips_server_and_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[server]/[push] must survive load() AND save() — the hand-rolled
    section map in config_file._parse once dropped them silently, leaving
    the daemon on loopback despite config.toml asking for the tailnet."""
    from nexus import config_file

    monkeypatch.setattr(config_file, "CONFIG_PATH", tmp_path / "config.toml")
    config_file.CONFIG_PATH.write_text(
        '[server]\nbind = "tailscale"\n'
        '[browser]\nremote_control = true\n'
        '[push.apns]\nkey_id = "ABC"\nteam_id = "TEAM"\n'
    )
    cfg = config_file.load()
    assert cfg.server.bind == "tailscale"
    assert cfg.browser.remote_control is True
    assert cfg.push.apns.key_id == "ABC"

    config_file.save(cfg)  # must not strip the sections back out
    cfg2 = config_file.load()
    assert cfg2.server.bind == "tailscale"
    assert cfg2.browser.remote_control is True
    assert cfg2.push.apns.key_id == "ABC"


def test_machine_name_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    assert pairing.machine_name("My Mac Pro") == "My Mac Pro"
    monkeypatch.setattr(pairing.platform, "node", lambda: "Ninos-MBP.local")
    assert pairing.machine_name("") == "Ninos-MBP"
    monkeypatch.setattr(pairing.platform, "node", lambda: "")
    assert pairing.machine_name("") == "Nexus"


def test_deep_link_round_trip() -> None:
    link = pairing.deep_link(name="Ninos Mac", host="100.64.10.20", port=18989, token="abc/def+")
    assert link.startswith("nexus://pair?")
    from urllib.parse import parse_qs, urlparse

    q = parse_qs(urlparse(link).query)
    assert q["n"] == ["Ninos Mac"]
    assert q["h"] == ["100.64.10.20"]
    assert q["p"] == ["18989"]
    assert q["t"] == ["abc/def+"]


# ── bind resolution unit tests ───────────────────────────────────────────────


def test_bind_loopback_default() -> None:
    assert netbind.resolve_bind_host("") == "127.0.0.1"
    assert netbind.resolve_bind_host("loopback") == "127.0.0.1"
    assert netbind.is_loopback_spec("localhost") is True


def test_bind_ip_literal() -> None:
    assert netbind.resolve_bind_host("192.168.1.50") == "192.168.1.50"
    assert netbind.is_loopback_spec("192.168.1.50") is False


def test_bind_tailscale_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(netbind, "tailscale_ipv4", lambda: "100.101.102.103")
    assert netbind.resolve_bind_host("tailscale") == "100.101.102.103"
    assert netbind.resolve_bind_host("vpn") == "100.101.102.103"


def test_bind_tailscale_unreachable_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(netbind, "tailscale_ipv4", lambda: None)
    with pytest.raises(netbind.BindError, match="[Tt]ailscale"):
        netbind.resolve_bind_host("tailscale")


def test_tailscale_ipv4_import_path_is_sound() -> None:
    """The real resolver must be import-clean (a broken relative import once
    made the supervisor silently treat every poll as 'disconnected')."""
    ip = netbind.tailscale_ipv4()  # may legitimately be None; must not raise
    assert ip is None or ip.startswith("100.")


def test_bind_invalid_raises() -> None:
    with pytest.raises(netbind.BindError):
        netbind.resolve_bind_host("999.999.1.1")


def test_scan_interfaces_filters_tailnet_range() -> None:
    # A fake ifconfig output: LAN address (skip) + tailnet address (hit).
    fake = (
        "lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384\n"
        "\tinet 127.0.0.1 netmask 0xff000000\n"
        "en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500\n"
        "\tinet 192.168.0.42 netmask 0xffffff00 broadcast 192.168.0.255\n"
        "utun4: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1380\n"
        "\tinet 100.77.4.12 --> 100.77.4.12 netmask 0xffffffff\n"
    )

    class _Proc:
        returncode = 0
        stdout = fake.encode()

    import subprocess

    real_run = subprocess.run

    def _fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if cmd and str(cmd[0]).endswith("ifconfig"):
            return _Proc()
        return real_run(cmd, **kwargs)

    import sys

    if sys.platform == "darwin":
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(netbind.subprocess, "run", _fake_run)
            assert netbind._scan_interfaces_ipv4() == "100.77.4.12"


# ── /pair + /status routes ───────────────────────────────────────────────────


def test_pair_prefers_https_serve_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a tailnet-only `tailscale serve` mapping exists, the QR must
    point at the https ts.net host — iOS 26's hardened ATS rejects
    cleartext to the CGNAT range for new-SDK apps."""
    from nexus.server.routes import pair as pair_route

    monkeypatch.setattr(pair_route, "_serve_host_cache", "nino-max.example.ts.net")
    client = _loopback_client()
    r = client.get("/pair")
    body = r.json()
    assert body["host"] == "nino-max.example.ts.net"
    assert body["port"] == 443
    assert body["tls"] is True
    assert body["url"] == "https://nino-max.example.ts.net"
    assert "tls=1" in body["deep_link"]


def test_pair_returns_descriptor_on_loopback() -> None:
    client = _loopback_client()
    r = client.get("/pair")
    assert r.status_code == 200
    body = r.json()
    assert body["v"] == 1
    assert body["host"] == "100.64.10.20"
    assert body["port"] == 18989
    assert body["url"] == "http://100.64.10.20:18989"
    assert body["tls"] is False
    assert len(body["token"]) >= 32
    assert body["deep_link"].startswith("nexus://pair?")
    assert "tls=1" not in body["deep_link"]
    assert body["name"]  # machine name resolved
    assert "features" in body


def test_pair_denied_from_tailnet_client() -> None:
    # Mint the token first so the middleware lets the request through to
    # the route (which must then 403 — pairing admin is loopback-only).
    token = pairing.get_or_create_token()
    client = _tailnet_client()
    r = client.get("/pair", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    # Without any token the middleware itself fails closed.
    r2 = _tailnet_client().get("/pair")
    assert r2.status_code == 401


def test_pair_rotate_changes_token() -> None:
    client = _loopback_client()
    first = client.get("/pair").json()["token"]
    second = client.post("/pair/rotate").json()["token"]
    assert second != first
    assert pairing.load_token() == second


def test_status_is_token_handshake() -> None:
    token = pairing.get_or_create_token()
    client = _tailnet_client()

    no_auth = client.get("/status")
    assert no_auth.status_code == 401

    ok = client.get("/status", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["ok"] is True
    assert body["machine_name"]
    assert body["tailnet_ip"] == "100.64.10.20"
    assert "browser" in body["features"]

    wrong = client.get("/status", headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401


def test_status_loopback_bypass() -> None:
    r = _loopback_client().get("/status")
    assert r.status_code == 200


def test_query_token_fallback_still_works() -> None:
    token = pairing.get_or_create_token()
    r = _tailnet_client().get(f"/status?token={token}")
    assert r.status_code == 200


# ── middleware: client token as a first-class credential ─────────────────────


def test_middleware_accepts_rotated_token_without_restart() -> None:
    token = pairing.get_or_create_token()
    client = _tailnet_client()
    assert client.get("/status", headers={"Authorization": f"Bearer {token}"}).status_code == 200

    pairing.rotate_token()
    # Old token revoked immediately (lazy mtime-checked reload).
    assert client.get("/status", headers={"Authorization": f"Bearer {token}"}).status_code == 401
