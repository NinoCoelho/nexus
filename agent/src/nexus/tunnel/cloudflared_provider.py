"""cloudflared provider — Cloudflare Quick Tunnel without account or signup.

Spawns ``cloudflared tunnel --url http://localhost:{port}`` and parses the
``*.trycloudflare.com`` URL out of the binary's stderr. The binary itself is
auto-downloaded on first use into ``~/.nexus/bin/`` so end-users don't have
to ``brew install`` anything — same lazy-install pattern that pyngrok used to
provide for ngrok.

Why cloudflared instead of ngrok: Cloudflare Quick Tunnels are anonymous and
free, with no signup, no authtoken, no dashboard step. The trade-off is the
URL randomizes on each start (same as ngrok-free), and the tunnel only lives
as long as the cloudflared process — both already match the user's mental
model in this app.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)


class CloudflaredError(RuntimeError):
    """Raised on any cloudflared install/start/stop failure."""


_BIN_DIR = Path.home() / ".nexus" / "bin"
_QUICK_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)
_RELEASE_BASE = "https://github.com/cloudflare/cloudflared/releases/latest/download"


def _binary_name() -> str:
    return "cloudflared.exe" if sys.platform == "win32" else "cloudflared"


def _binary_path() -> Path:
    return _BIN_DIR / _binary_name()


def _release_asset() -> tuple[str, bool]:
    """Pick the right cloudflared release asset for this OS+arch.

    Returns ``(filename, is_tarball)``. macOS ships only as a .tgz; Linux and
    Windows ship the binary directly.
    """
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        if machine in ("arm64", "aarch64"):
            return "cloudflared-darwin-arm64.tgz", True
        return "cloudflared-darwin-amd64.tgz", True
    if sys.platform.startswith("linux"):
        if machine in ("aarch64", "arm64"):
            return "cloudflared-linux-arm64", False
        if machine in ("armv7l", "armv6l", "arm"):
            return "cloudflared-linux-arm", False
        return "cloudflared-linux-amd64", False
    if sys.platform == "win32":
        if machine in ("arm64", "aarch64"):
            return "cloudflared-windows-arm64.exe", False
        return "cloudflared-windows-amd64.exe", False
    raise CloudflaredError(
        f"Unsupported platform for cloudflared: {sys.platform}/{machine}",
    )


def binary_installed() -> bool:
    p = _binary_path()
    return p.is_file() and os.access(p, os.X_OK)


def install_binary() -> Path:
    """Idempotently fetch the cloudflared binary into ``~/.nexus/bin/``.

    Safe to call repeatedly; returns immediately if the binary is already
    in place. Raises ``CloudflaredError`` with a readable message on network
    or extraction failure.
    """
    target = _binary_path()
    if target.is_file() and os.access(target, os.X_OK):
        log.info("cloudflared already installed at %s", target)
        return target

    asset, is_tarball = _release_asset()
    url = f"{_RELEASE_BASE}/{asset}"
    _BIN_DIR.mkdir(parents=True, exist_ok=True)

    log.info("downloading cloudflared from %s", url)
    try:
        with tempfile.NamedTemporaryFile(delete=False, dir=_BIN_DIR) as tmp:
            tmp_path = Path(tmp.name)
            req = urllib.request.Request(url, headers={"User-Agent": "nexus-tunnel"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                shutil.copyfileobj(resp, tmp)
    except Exception as e:
        raise CloudflaredError(
            "Could not download cloudflared from GitHub. Check your internet "
            f"connection. Underlying error: {e}",
        ) from e

    try:
        if is_tarball:
            with tarfile.open(tmp_path, "r:gz") as tf:
                member = next(
                    (m for m in tf.getmembers() if m.name.endswith("cloudflared")),
                    None,
                )
                if member is None:
                    raise CloudflaredError(
                        f"cloudflared binary not found inside {asset}",
                    )
                src = tf.extractfile(member)
                if src is None:
                    raise CloudflaredError(f"could not extract cloudflared from {asset}")
                with open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
            tmp_path.unlink(missing_ok=True)
        else:
            os.replace(tmp_path, target)
        os.chmod(target, 0o755)
    except CloudflaredError:
        raise
    except Exception as e:
        raise CloudflaredError(f"Failed to install cloudflared: {e}") from e

    log.info("cloudflared installed at %s", target)
    return target


def _read_url_from_stderr(proc: subprocess.Popen[bytes], timeout: float) -> str:
    """Block until cloudflared prints its trycloudflare.com URL or we time out.

    cloudflared writes startup info to stderr (it reserves stdout for proxied
    request bodies in some modes). We scan for the first match of the quick-
    tunnel URL pattern, then keep a daemon thread draining stderr so the pipe
    buffer doesn't fill and stall the child.
    """
    if proc.stderr is None:
        raise CloudflaredError("cloudflared started without a stderr pipe")

    deadline = time.monotonic() + timeout
    found_url: list[str] = []
    found_event = threading.Event()
    fail_lines: list[str] = []

    def _drain() -> None:
        assert proc.stderr is not None
        for raw in proc.stderr:
            try:
                line = raw.decode("utf-8", errors="replace")
            except Exception:
                continue
            if not found_event.is_set():
                m = _QUICK_URL_RE.search(line)
                if m:
                    found_url.append(m.group(0))
                    found_event.set()
                else:
                    # Keep last few lines around for error reporting.
                    fail_lines.append(line.rstrip())
                    if len(fail_lines) > 20:
                        fail_lines.pop(0)
            # After the URL is found we just keep draining to /dev/null.

    t = threading.Thread(target=_drain, daemon=True, name="cloudflared-stderr")
    t.start()

    while time.monotonic() < deadline:
        if found_event.wait(timeout=0.25):
            return found_url[0]
        if proc.poll() is not None:
            tail = "\n".join(fail_lines[-10:])
            raise CloudflaredError(
                f"cloudflared exited before the tunnel URL appeared (rc={proc.returncode}). "
                f"stderr tail:\n{tail}",
            )

    raise CloudflaredError(
        f"timed out waiting for cloudflared to print the tunnel URL (>{timeout:.0f}s)",
    )


def start_tunnel(*, port: int, timeout: float = 30.0) -> tuple[subprocess.Popen[bytes], str]:
    """Spawn cloudflared and return ``(process, public_url)``.

    Auto-installs the binary on first use. The caller owns the returned
    ``Popen`` handle and must pass it back to ``stop_tunnel`` to clean up.
    """
    install_binary()
    binary = _binary_path()
    cmd = [
        str(binary),
        "tunnel",
        "--no-autoupdate",
        "--metrics", "127.0.0.1:0",
        "--url", f"http://localhost:{port}",
    ]
    log.info("starting cloudflared: %s", " ".join(cmd))
    try:
        proc = subprocess.Popen(  # noqa: S603 — bin path is under our control
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as e:
        raise CloudflaredError(f"cloudflared binary missing at {binary}") from e
    except Exception as e:
        raise CloudflaredError(f"failed to spawn cloudflared: {e}") from e

    try:
        url = _read_url_from_stderr(proc, timeout=timeout)
    except CloudflaredError:
        # Best-effort cleanup if startup failed mid-flight.
        stop_tunnel(proc)
        raise

    log.info("cloudflared tunnel up: %s -> http://localhost:%d", url, port)
    return proc, url


def stop_tunnel(proc: subprocess.Popen[bytes] | None) -> None:
    """Tear down the cloudflared subprocess. Best-effort, never raises."""
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    except Exception:
        log.exception("cloudflared stop failed")
