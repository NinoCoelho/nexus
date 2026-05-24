"""Self-update mechanism for the llama.cpp server binary.

Checks the latest llama.cpp GitHub release, downloads the platform-appropriate
archive into ``~/.nexus/llama/``, and cleans up old versions.  Rate-limited
to at most one remote check per 24 hours.
"""

from __future__ import annotations

import json
import logging
import platform
import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.request import urlopen, Request

log = logging.getLogger(__name__)

_LLAMA_DIR = Path.home() / ".nexus" / "llama"
_CHECK_FILE = _LLAMA_DIR / ".last-check"
_CHECK_INTERVAL = 86400  # 24 h

_GH_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
_GH_DL = "https://github.com/ggml-org/llama.cpp/releases/download"

_DOWNLOADING = False


def _platform_dist(tag: str) -> tuple[str, str]:
    """Return ``(filename, human_label)`` for the current platform."""
    m = platform.machine()
    is_arm = m in ("arm64", "aarch64")
    if sys.platform == "darwin":
        if is_arm:
            return f"llama-{tag}-bin-macos-arm64.tar.gz", "macOS arm64 (Metal)"
        return f"llama-{tag}-bin-macos-x64.tar.gz", "macOS x64"
    if sys.platform == "win32":
        if is_arm:
            return f"llama-{tag}-bin-win-cpu-arm64.zip", "Windows arm64"
        return f"llama-{tag}-bin-win-cpu-x64.zip", "Windows x64 (CPU)"
    if sys.platform == "linux":
        return f"llama-{tag}-bin-linux-amd64.tar.gz", "Linux amd64"
    return "", "unknown"


def current_version() -> int:
    """Return the build number of the currently selected llama-server binary.

    Returns 0 if no binary is found or the version cannot be determined.
    """
    from .manager import discover_binary

    binary = discover_binary()
    if binary is None:
        return 0
    return _version_from_path(binary)


def _version_from_path(p: Path) -> int:
    import re
    for text in (p.parent.name, p.name):
        m = re.search(r"b(\d+)", text)
        if m:
            return int(m.group(1))
    return 0


def _cached_latest() -> dict[str, Any] | None:
    """Return cached latest-release info if still fresh, else None."""
    if not _CHECK_FILE.exists():
        return None
    try:
        data = json.loads(_CHECK_FILE.read_text())
        import time
        if time.time() - data.get("ts", 0) < _CHECK_INTERVAL:
            return data
    except (OSError, ValueError, KeyError):
        pass
    return None


def _save_latest(data: dict[str, Any]) -> None:
    import time
    data["ts"] = time.time()
    _LLAMA_DIR.mkdir(parents=True, exist_ok=True)
    _CHECK_FILE.write_text(json.dumps(data))


def check_latest() -> dict[str, Any] | None:
    """Check for the latest llama.cpp release (rate-limited to once / 24 h).

    Returns ``{"tag": "b9222", "version": 9222, "dist": "...", "label": "..."}``
    or ``None`` if the check fails or is rate-limited.
    """
    cached = _cached_latest()
    if cached is not None:
        tag = cached.get("tag", "")
        if tag:
            dist, label = _platform_dist(tag)
            return {"tag": tag, "version": cached.get("version", 0), "dist": dist, "label": label}

    try:
        req = Request(_GH_API, headers={"User-Agent": "nexus/1.0"})
        with urlopen(req, timeout=10) as resp:
            release = json.loads(resp.read())
        tag = release.get("tag_name", "")
        if not tag:
            return None
        import re
        m = re.search(r"b(\d+)", tag)
        version = int(m.group(1)) if m else 0
        info = {"tag": tag, "version": version}
        _save_latest(info)
        dist, label = _platform_dist(tag)
        return {"tag": tag, "version": version, "dist": dist, "label": label}
    except Exception:
        log.debug("llama.cpp version check failed", exc_info=True)
        return None


def needs_update() -> dict[str, Any] | None:
    """Return update info if a newer version is available, else ``None``."""
    cur = current_version()
    if cur == 0:
        return None
    latest = check_latest()
    if latest is None or latest["version"] <= cur:
        return None
    return {
        "current": cur,
        "latest": latest["version"],
        "tag": latest["tag"],
        "dist": latest["dist"],
        "label": latest["label"],
        "url": f"{_GH_DL}/{latest['tag']}/{latest['dist']}",
    }


def download_and_install(url: str, tag: str, dist: str) -> Path:
    """Download a llama-server release and install it under ``~/.nexus/llama/``.

    Returns the path to the installed ``llama-server`` binary.
    Raises on failure.
    """
    global _DOWNLOADING
    _DOWNLOADING = True
    try:
        dest_dir = _LLAMA_DIR / f"llama-{tag}"
        if dest_dir.exists():
            binary = _find_server(dest_dir)
            if binary is not None:
                return binary

        _LLAMA_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=Path(dist).suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            log.info("[binary_update] downloading %s …", dist)
            req = Request(url, headers={"User-Agent": "nexus/1.0"})
            with urlopen(req, timeout=300) as resp:
                total = int(resp.headers.get("content-length", 0))
                done = 0
                while True:
                    chunk = resp.read(1 << 20)  # 1 MB
                    if not chunk:
                        break
                    tmp.write(chunk)
                    done += len(chunk)
                    if total > 0:
                        _publish_progress(tag, done, total)

        log.info("[binary_update] extracting to %s", dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        if dist.endswith(".tar.gz") or dist.endswith(".tgz"):
            with tarfile.open(tmp_path) as tf:
                members = tf.getmembers()
                if members and members[0].isdir():
                    strip = len(members[0].name) + 1
                else:
                    strip = 0
                for m in members:
                    if strip:
                        m.name = m.name[strip:]
                        m.path = m.name
                    tf.extract(m, dest_dir, filter="data", set_attrs=False)
        elif dist.endswith(".zip"):
            with zipfile.ZipFile(tmp_path) as zf:
                zf.extractall(dest_dir)
        tmp_path.unlink(missing_ok=True)

        binary = _find_server(dest_dir)
        if binary is None:
            raise FileNotFoundError(f"llama-server not found in {dest_dir}")
        binary.chmod(binary.stat().st_mode | 0o111)

        cleanup_old(keep_current=tag)
        log.info("[binary_update] installed %s → %s", tag, binary)
        return binary
    finally:
        _DOWNLOADING = False


def cleanup_old(keep_current: str = "") -> list[Path]:
    """Remove old ``llama-b*`` directories, keeping the current version.

    Returns list of removed paths.
    """
    import re
    if not _LLAMA_DIR.is_dir():
        return []
    removed = []
    keep_name = f"llama-{keep_current}" if keep_current else ""
    current_bin = None
    try:
        from .manager import discover_binary
        found = discover_binary()
        if found:
            current_bin = found.parent.name
    except Exception:
        pass
    for d in sorted(_LLAMA_DIR.iterdir()):
        if not d.is_dir():
            continue
        if not re.match(r"llama-b\d+", d.name):
            continue
        if d.name == keep_name or d.name == current_bin:
            continue
        try:
            shutil.rmtree(d)
            removed.append(d)
            log.info("[binary_update] removed old %s", d)
        except OSError:
            pass
    return removed


def is_downloading() -> bool:
    return _DOWNLOADING


def _find_server(directory: Path) -> Path | None:
    for candidate in directory.rglob("llama-server*"):
        if candidate.is_file():
            return candidate
    return None


def _publish_progress(tag: str, done: int, total: int) -> None:
    try:
        from ..server import event_bus
        event_bus.publish({
            "kind": "local_llm.binary_update.progress",
            "tag": tag,
            "downloaded_bytes": done,
            "total_bytes": total,
        })
    except Exception:
        pass
