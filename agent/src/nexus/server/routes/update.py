"""Update routes — check, download, install, skip.

Queries the GitHub Releases API for ``NinoCoelho/nexus`` and manages the
download + install lifecycle. All state is kept in ``~/.nexus/tmp/update/``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from enum import Enum
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import nexus

router = APIRouter(prefix="/update", tags=["update"])

log = logging.getLogger(__name__)

GITHUB_REPO = "NinoCoelho/nexus"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
CACHE_TTL = 3600  # 1 hour

NEXUS_HOME = Path.home() / ".nexus"
UPDATE_DIR = NEXUS_HOME / "tmp" / "update"
SKIP_FILE = NEXUS_HOME / "skipped_update.json"
STATE_FILE = UPDATE_DIR / "state.json"

_current_platform = "macos" if sys.platform == "darwin" else "windows"


class UpdateState(str, Enum):
    idle = "idle"
    downloading = "downloading"
    ready = "ready"
    installing = "installing"
    error = "error"


class AssetInfo(BaseModel):
    name: str
    browser_download_url: str
    size: int


class UpdateCheckResult(BaseModel):
    current: str
    latest: str
    update_available: bool
    skipped: bool = False
    html_url: str = ""
    body: str = ""
    assets: list[AssetInfo] = Field(default_factory=list)


class SkipRequest(BaseModel):
    version: str


class InstallResponse(BaseModel):
    status: str
    message: str


_cached_release: dict[str, Any] | None = None
_cached_at: float = 0

_download_task: asyncio.Task | None = None
_download_progress: float = 0.0
_download_error: str = ""


def _parse_version(tag: str) -> tuple[int, ...]:
    v = tag.lstrip("vV")
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _read_skipped() -> str:
    try:
        data = json.loads(SKIP_FILE.read_text())
        return data.get("skipped", "")
    except (OSError, json.JSONDecodeError):
        return ""


def _read_state() -> UpdateState:
    try:
        data = json.loads(STATE_FILE.read_text())
        return UpdateState(data.get("state", "idle"))
    except (OSError, json.JSONDecodeError, ValueError):
        return UpdateState.idle


def _write_state(state: UpdateState, **extra: Any) -> None:
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    data = {"state": state.value, **extra}
    STATE_FILE.write_text(json.dumps(data))


def _fetch_latest_release() -> dict[str, Any] | None:
    global _cached_release, _cached_at
    import time

    now = time.monotonic()
    if _cached_release is not None and (now - _cached_at) < CACHE_TTL:
        return _cached_release

    req = urllib.request.Request(
        GITHUB_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "nexus"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            release = json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        log.warning("update check failed: %s", exc)
        return _cached_release

    _cached_release = release
    _cached_at = now
    return release


def _pick_asset(release: dict[str, Any]) -> dict[str, Any] | None:
    assets = release.get("assets", [])
    if _current_platform == "macos":
        suffix = ".pkg"
    else:
        suffix = "-windows-x64.zip"
    for a in assets:
        if a["name"].endswith(suffix):
            return a
    return None


@router.get("/check", response_model=UpdateCheckResult)
async def check_updates() -> UpdateCheckResult:
    current = nexus.__version__
    release = _fetch_latest_release()
    if release is None:
        return UpdateCheckResult(current=current, latest=current, update_available=False)

    tag = release.get("tag_name", "")
    latest = tag.lstrip("vV")
    update_available = _parse_version(tag) > _parse_version(current)
    skipped = False
    if update_available:
        skipped_version = _read_skipped()
        if skipped_version == latest:
            skipped = True

    assets = [
        AssetInfo(
            name=a["name"],
            browser_download_url=a["browser_download_url"],
            size=a.get("size", 0),
        )
        for a in release.get("assets", [])
    ]

    return UpdateCheckResult(
        current=current,
        latest=latest,
        update_available=update_available and not skipped,
        skipped=skipped,
        html_url=release.get("html_url", ""),
        body=release.get("body", ""),
        assets=assets,
    )


@router.post("/download")
async def download_update() -> StreamingResponse:
    global _download_task, _download_progress, _download_error

    state = _read_state()
    if state == UpdateState.downloading:
        return StreamingResponse(
            _stream_progress(),
            media_type="text/event-stream",
        )

    release = _fetch_latest_release()
    if release is None:
        raise HTTPException(502, "could not fetch release info")

    asset = _pick_asset(release)
    if asset is None:
        raise HTTPException(404, "no matching asset for this platform")

    tag = release.get("tag_name", "")
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPDATE_DIR / asset["name"]

    if dest.is_file() and _read_state() == UpdateState.ready:
        return StreamingResponse(
            _stream_done(dest),
            media_type="text/event-stream",
        )

    _download_progress = 0.0
    _download_error = ""
    _write_state(UpdateState.downloading, file=str(dest), tag=tag)

    async def _do_download() -> None:
        global _download_progress, _download_error
        try:
            tmp = dest.with_suffix(".tmp")
            req = urllib.request.Request(
                asset["browser_download_url"],
                headers={"User-Agent": "nexus"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp, open(tmp, "wb") as f:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                while True:
                    chunk = resp.read(1 << 16)  # 64 KB
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    _download_progress = downloaded / total if total else 0
            tmp.rename(dest)
            _write_state(UpdateState.ready, file=str(dest), tag=tag)
            _download_progress = 1.0
        except Exception as exc:
            _download_error = str(exc)
            _write_state(UpdateState.error, error=str(exc))
            log.exception("update download failed")

    loop = asyncio.get_running_loop()
    _download_task = loop.create_task(loop.run_in_executor(None, lambda: asyncio.run(_do_download())))

    return StreamingResponse(
        _stream_progress(),
        media_type="text/event-stream",
    )


async def _stream_progress():
    while True:
        state = _read_state()
        if state == UpdateState.ready:
            yield f"data: {json.dumps({'state': 'done', 'progress': 1.0})}\n\n"
            return
        if state == UpdateState.error:
            yield f"data: {json.dumps({'state': 'error', 'error': _download_error})}\n\n"
            return
        yield f"data: {json.dumps({'state': 'downloading', 'progress': round(_download_progress, 3)})}\n\n"
        await asyncio.sleep(0.5)


async def _stream_done(dest: Path):
    yield f"data: {json.dumps({'state': 'done', 'progress': 1.0, 'file': str(dest)})}\n\n"


@router.get("/status")
async def update_status() -> dict[str, Any]:
    state = _read_state()
    result: dict[str, Any] = {"state": state.value}
    try:
        data = json.loads(STATE_FILE.read_text())
        result["file"] = data.get("file", "")
        result["tag"] = data.get("tag", "")
        result["error"] = data.get("error", "")
    except (OSError, json.JSONDecodeError):
        pass
    if state == UpdateState.downloading:
        result["progress"] = round(_download_progress, 3)
    return result


@router.post("/install", response_model=InstallResponse)
async def install_update() -> InstallResponse:
    state = _read_state()
    if state != UpdateState.ready:
        raise HTTPException(400, f"no update ready (state={state.value})")

    try:
        data = json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        raise HTTPException(500, "corrupt update state")

    update_file = Path(data.get("file", ""))
    if not update_file.is_file():
        raise HTTPException(404, "downloaded file not found")

    _write_state(UpdateState.installing, file=str(update_file))

    if _current_platform == "macos":
        _install_macos(update_file)
    else:
        _install_windows(update_file)

    return InstallResponse(
        status="installing",
        message="Update triggered — the app will quit and install.",
    )


def _install_macos(pkg_path: Path) -> None:
    import subprocess

    script = f"""#!/bin/bash
sleep 2
open "{pkg_path}"
"""
    script_path = UPDATE_DIR / "install.sh"
    script_path.write_text(script)
    script_path.chmod(0o755)
    subprocess.Popen(
        ["/bin/bash", str(script_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    import signal
    os.kill(os.getpid(), signal.SIGTERM)


def _install_windows(zip_path: Path) -> None:
    install_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()

    script = f"""@echo off
echo Waiting for Nexus to exit...
:wait
tasklist /fi "pid eq {os.getpid()}" 2>nul | find "{os.getpid()}" >nul
if not errorlevel 1 (
    timeout /t 2 /nobreak >nul
    goto wait
)
echo Extracting update...
powershell -Command "Expand-Archive -Path '{zip_path}' -DestinationPath '{install_dir.parent}' -Force"
echo Starting Nexus...
start "" "{install_dir / 'Nexus.exe'}"
del "%~f0"
"""
    script_path = UPDATE_DIR / "update.bat"
    script_path.write_text(script)

    import subprocess
    subprocess.Popen(
        ["cmd", "/c", str(script_path)],
        cwd=str(UPDATE_DIR),
        creationflags=0x08000000,
        close_fds=True,
    )
    import signal
    os.kill(os.getpid(), signal.SIGTERM)


@router.post("/skip")
async def skip_version(req: SkipRequest) -> dict[str, str]:
    NEXUS_HOME.mkdir(parents=True, exist_ok=True)
    SKIP_FILE.write_text(json.dumps({"skipped": req.version}))
    return {"status": "ok", "skipped": req.version}


@router.post("/reset-skip")
async def reset_skip() -> dict[str, str]:
    try:
        SKIP_FILE.unlink()
    except OSError:
        pass
    return {"status": "ok"}
