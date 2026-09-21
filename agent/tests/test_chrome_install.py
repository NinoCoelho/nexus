"""Tests for `nexus chrome install` file copy + doctor logic.

Runs against tmp dirs (monkeypatched DEST_DIR) — never the user's real
~/.nexus/chrome-extension.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus import chrome_install


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    src = tmp_path / "src"
    (src / "icons").mkdir(parents=True)
    (src / "manifest.json").write_text(json.dumps({"version": "9.9.9", "name": "test"}))
    (src / "panel.html").write_text("<html></html>")
    (src / "icons" / "icon128.png").write_bytes(b"\x89PNG")
    dest = tmp_path / "dest"
    monkeypatch.setattr(chrome_install, "DEST_DIR", dest)
    monkeypatch.setattr(chrome_install, "_source_dir", lambda: src)
    monkeypatch.setattr(chrome_install, "_server_port", lambda: 18989)
    return {"src": src, "dest": dest}


def test_install_copies_tree(env: dict[str, Path]) -> None:
    assert chrome_install.install(open_browser=False) == 0
    dest = env["dest"]
    assert (dest / "manifest.json").exists()
    assert (dest / "panel.html").exists()
    assert (dest / "icons" / "icon128.png").read_bytes() == b"\x89PNG"


def test_install_replaces_previous(env: dict[str, Path]) -> None:
    chrome_install.install(open_browser=False)
    stale = env["dest"] / "stale.txt"
    stale.write_text("old")
    chrome_install.install(open_browser=False)
    assert not stale.exists()
    assert (env["dest"] / "manifest.json").exists()


def test_install_missing_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chrome_install, "_source_dir", lambda: None)
    assert chrome_install.install(open_browser=False) == 1


def test_doctor_reports_missing_extension(env: dict[str, Path]) -> None:
    assert chrome_install.doctor() == 1


def test_doctor_green_with_files_and_heartbeat(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    chrome_install.install(open_browser=False)

    def fake_get(url: str, headers=None, timeout=3.0):
        if url.endswith("/health"):
            return {"ok": True}
        if url.endswith("/ext/ping"):
            return {"ok": True, "extension_seen": True, "age_seconds": 1.0, "version": "9.9.9"}
        return None

    monkeypatch.setattr(chrome_install, "_get", fake_get)
    assert chrome_install.doctor() == 0
