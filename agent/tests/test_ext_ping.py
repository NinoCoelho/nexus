"""Tests for the /ext endpoints (extension heartbeat + install page).

The heartbeat records state only when the extension header is present —
polling reads must never overwrite last-seen. The install page carries
the `nexus-server` meta marker the extension's port-capture script keys
on, and shows the expected extension directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus.server.routes.ext import router


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
    from nexus.server.routes.ext import extension_dir

    assert str(extension_dir()) in body
    assert "chrome://extensions" in body
    _ = Path.home()


def test_files_endpoint_reports_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexus import chrome_install

    monkeypatch.setattr(chrome_install, "DEST_DIR", tmp_path / "ext")
    client = _client()
    missing = client.get("/ext/files").json()
    assert missing["ok"] is True
    assert missing["installed"] is False

    (tmp_path / "ext").mkdir()
    (tmp_path / "ext" / "manifest.json").write_text('{"version": "1.2.3"}')
    present = client.get("/ext/files").json()
    assert present["installed"] is True
    assert present["version"] == "1.2.3"


def test_prepare_endpoint_runs_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexus import chrome_install

    monkeypatch.setattr(chrome_install, "DEST_DIR", tmp_path / "ext")
    calls: list[bool] = []
    monkeypatch.setattr(
        chrome_install, "install", lambda open_browser=True: calls.append(open_browser) or 0
    )
    monkeypatch.setattr(chrome_install, "_source_dir", lambda: tmp_path)
    client = _client()
    res = client.post("/ext/prepare").json()
    assert res["ok"] is True
    assert calls == [False]


def test_prepare_endpoint_reports_missing_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexus import chrome_install

    monkeypatch.setattr(chrome_install, "_source_dir", lambda: None)
    client = _client()
    res = client.post("/ext/prepare").json()
    assert res["ok"] is False
    assert "sources not found" in res["error"]
