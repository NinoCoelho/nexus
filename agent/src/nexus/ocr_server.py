"""Internal OCR model lifecycle — separate from the user-facing model infrastructure.

Manages a dedicated llama-server for the bundled OCR model (chandra-ocr-2)
that is invisible to the model picker, settings UI, and provider registry.
The server lives in ``~/.nexus/ocr-model/`` and is started/stopped
independently of any chat models the user installs.

Resolution order for OCR:

1. External vision model (``cfg.agent.vision_model``) — cloud or local model
   configured by the user via Settings.
2. Bundled chandra server (this module) — auto-downloaded on first use.
3. RapidOCR / Tesseract — local engines.
4. Empty — OCR unavailable.

GPU contention: when the user starts a local chat model, ``pause()`` is
called to free VRAM. ``resume()`` restarts the server when the chat model
stops.
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_OCR_MODEL_DIR = Path.home() / ".nexus" / "ocr-model"
_MODEL_FILENAME = "chandra-ocr-2.Q8_0.gguf"
_MMPROJ_FILENAME = "chandra-ocr-2.mmproj-q8_0.gguf"
_REPO_ID = "prithivMLmods/chandra-ocr-2-GGUF"
_CTX_SIZE = 8192
_HEALTH_TIMEOUT = 90.0

_server_proc: subprocess.Popen | None = None
_server_port: int = 0
_paused: bool = False

_DOWNLOAD_PROGRESS: dict[str, Any] = {}


def model_dir() -> Path:
    return _OCR_MODEL_DIR


def model_files_exist() -> bool:
    return (_OCR_MODEL_DIR / _MODEL_FILENAME).is_file()


def mmproj_exists() -> bool:
    return (_OCR_MODEL_DIR / _MMPROJ_FILENAME).is_file()


def is_installed() -> bool:
    return model_files_exist() and mmproj_exists()


def is_running() -> bool:
    global _server_proc
    if _server_proc is None:
        return False
    if _server_proc.poll() is not None:
        _server_proc = None
        return False
    return True


def is_paused() -> bool:
    return _paused


def base_url() -> str | None:
    if not is_running():
        return None
    return f"http://127.0.0.1:{_server_port}"


def status() -> dict[str, Any]:
    downloading = bool(_DOWNLOAD_PROGRESS)
    progress = 0.0
    if downloading:
        total = _DOWNLOAD_PROGRESS.get("total_bytes", 0)
        done = _DOWNLOAD_PROGRESS.get("downloaded_bytes", 0)
        if total > 0:
            progress = done / total
    return {
        "installed": is_installed(),
        "running": is_running(),
        "paused": _paused,
        "downloading": downloading,
        "progress": progress,
        "port": _server_port if is_running() else None,
    }


def _pick_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _discover_binary() -> Path | None:
    env_bin = os.environ.get("NEXUS_LLAMA_BIN", "")
    if env_bin:
        p = Path(env_bin)
        if p.is_file():
            return p

    bundle_dir = os.environ.get("NEXUS_BUNDLE_DIR", "")
    if bundle_dir:
        for candidate in Path(bundle_dir).glob("llama/**/llama-server"):
            if candidate.is_file():
                return candidate

    user_llama = Path.home() / ".nexus" / "llama"
    if user_llama.is_dir():
        for candidate in user_llama.glob("**/llama-server"):
            if candidate.is_file():
                return candidate

    which = shutil.which("llama-server")
    return Path(which) if which else None


def _wait_health(port: int, proc: subprocess.Popen) -> bool:
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:{port}/health"
    deadline = time.time() + _HEALTH_TIMEOUT
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=2.0) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    return False


def start() -> bool:
    global _server_proc, _server_port, _paused

    if is_running():
        _paused = False
        return True

    if not is_installed():
        log.warning("[ocr_server] model not installed — call download_if_missing() first")
        return False

    binary = _discover_binary()
    if binary is None:
        log.warning("[ocr_server] llama-server binary not found")
        return False

    model_path = _OCR_MODEL_DIR / _MODEL_FILENAME
    mmproj_path = _OCR_MODEL_DIR / _MMPROJ_FILENAME

    port = _pick_free_port()

    if sys_platform := _get_log_path():
        log_path = sys_platform
    else:
        log_path = None

    log_handle = None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = open(log_path, "ab")

    cmd = [
        str(binary),
        "-m", str(model_path),
        "--mmproj", str(mmproj_path),
        "--host", "127.0.0.1",
        "--port", str(port),
        "-c", str(_CTX_SIZE),
        "-ngl", "99",
        "--jinja",
        "--parallel", "1",
    ]

    log.info("[ocr_server] starting: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=log_handle if log_handle else subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )

    if not _wait_health(port, proc):
        log.warning("[ocr_server] server failed to become ready")
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass
        return False

    _server_proc = proc
    _server_port = port
    _paused = False
    log.info("[ocr_server] ready on :%d", port)
    return True


def stop() -> None:
    global _server_proc, _paused
    if _server_proc is None:
        return
    try:
        _server_proc.terminate()
        try:
            _server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _server_proc.kill()
            _server_proc.wait()
    except Exception:
        log.exception("[ocr_server] error stopping")
    _server_proc = None
    _paused = False
    log.info("[ocr_server] stopped")


def pause() -> None:
    if not is_running():
        return
    log.info("[ocr_server] pausing (freeing GPU for chat model)")
    stop()
    global _paused
    _paused = True


def resume() -> None:
    global _paused
    if not _paused:
        return
    log.info("[ocr_server] resuming (chat model stopped)")
    _paused = False
    start()


def download_if_missing() -> bool:
    if is_installed():
        return True

    _OCR_MODEL_DIR.mkdir(parents=True, exist_ok=True)

    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        log.warning("[ocr_server] huggingface_hub not installed — cannot download OCR model")
        return False

    global _DOWNLOAD_PROGRESS

    for filename in (_MODEL_FILENAME, _MMPROJ_FILENAME):
        target = _OCR_MODEL_DIR / filename
        if target.is_file():
            continue

        _DOWNLOAD_PROGRESS = {"filename": filename, "total_bytes": 0, "downloaded_bytes": 0}
        log.info("[ocr_server] downloading %s from %s", filename, _REPO_ID)

        try:
            downloaded = hf_hub_download(
                repo_id=_REPO_ID,
                filename=filename,
                local_dir=str(_OCR_MODEL_DIR),
            )
            src = Path(downloaded)
            if src != target:
                import shutil as _shutil
                _shutil.move(str(src), str(target))

            size = target.stat().st_size
            _DOWNLOAD_PROGRESS = {"filename": filename, "total_bytes": size, "downloaded_bytes": size}
            log.info("[ocr_server] downloaded %s (%d bytes)", filename, size)
        except Exception:
            log.exception("[ocr_server] download failed for %s", filename)
            _DOWNLOAD_PROGRESS = {}
            return False

    _DOWNLOAD_PROGRESS = {}
    log.info("[ocr_server] model installed")
    return True


async def download_if_missing_async() -> bool:
    import asyncio
    return await asyncio.to_thread(download_if_missing)


def migrate_from_local_llm() -> bool:
    old_dir = Path.home() / ".nexus" / "models" / "llm"
    moved = False

    for filename in (_MODEL_FILENAME, _MMPROJ_FILENAME):
        src = old_dir / filename
        dst = _OCR_MODEL_DIR / filename
        if src.is_file() and not dst.is_file():
            _OCR_MODEL_DIR.mkdir(parents=True, exist_ok=True)
            import shutil as _shutil
            _shutil.move(str(src), str(dst))
            log.info("[ocr_server] migrated %s from models/llm/ to ocr-model/", filename)
            moved = True

    return moved


def cleanup_config_entries() -> None:
    from .local_llm.manager import _load_config, _save_config

    cfg_path, raw = _load_config()

    providers = raw.get("providers", {})
    changed = False

    for pname in list(providers.keys()):
        if "chandra" in pname.lower():
            providers.pop(pname, None)
            changed = True

    models = raw.get("models", [])
    new_models = [m for m in models if "chandra" not in m.get("id", "").lower() and "chandra" not in m.get("provider", "").lower()]
    if len(new_models) != len(models):
        raw["models"] = new_models
        changed = True

    agent_cfg = raw.get("agent", {})
    vm = agent_cfg.get("vision_model", "")
    if vm and "chandra" in vm.lower():
        agent_cfg["vision_model"] = ""
        changed = True

    if changed:
        _save_config(cfg_path, raw)
        log.info("[ocr_server] cleaned chandra entries from config.toml")


def _get_log_path() -> Path | None:
    import sys

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "Nexus" / "ocr-server.log"
    elif sys.platform == "win32":
        return Path.home() / "AppData" / "Local" / "Nexus" / "logs" / "ocr-server.log"
    return None
