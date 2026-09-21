"""Tests for the /ext endpoints (extension heartbeat + install page).

The heartbeat records state only when the extension header is present —
polling reads must never overwrite last-seen. The install page carries
the `nexus-server` meta marker the extension's port-capture script keys
on, and shows the expected extension directory.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus.server.routes.ext import extension_dir, router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_ping_without_header_does_not_record() -> None:
    client = _client()
    res = client.get("/ext/ping")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["extension_seen"] is False
    assert data["age_seconds"] is None


def test_ping_with_header_records_and_reads_back() -> None:
    client = _client()
    res = client.get("/ext/ping", headers={"X-Nexus-Extension": "0.13.0"})
    assert res.status_code == 200
    assert res.json()["extension_seen"] is True
    assert res.json()["version"] == "0.13.0"

    follow = client.get("/ext/ping").json()
    assert follow["extension_seen"] is True
    assert follow["version"] == "0.13.0"
    assert isinstance(follow["age_seconds"], float)


def test_install_page_has_marker_and_path() -> None:
    client = _client()
    res = client.get("/ext")
    assert res.status_code == 200
    body = res.text
    assert '<meta name="nexus-server" content="1">' in body
    assert str(extension_dir()) in body
    assert "chrome://extensions" in body
    _ = Path.home()
