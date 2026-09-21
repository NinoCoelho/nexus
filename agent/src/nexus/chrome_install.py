"""``nexus chrome install | doctor`` — side-panel extension setup.

Local-only distribution: the extension lives at ``~/.nexus/chrome-extension``
(loaded unpacked by the user); installers call ``nexus chrome install`` in
their post-install step, which copies the bundled files and opens the guided
``/ext`` page. ``doctor`` verifies server, files, and extension heartbeat.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

DEFAULT_PORT = 18989
DEST_DIR = Path.home() / ".nexus" / "chrome-extension"


def _source_dir() -> Path | None:
    env = os.environ.get("NEXUS_CHROME_DIR")
    if env and (Path(env) / "manifest.json").exists():
        return Path(env)
    pkg = Path(__file__).parent / "chrome_ext"
    if (pkg / "manifest.json").exists():
        return pkg
    app = Path(sys.prefix).parent / "chrome-extension-src"
    if (app / "manifest.json").exists():
        return app
    dev = Path(__file__).resolve().parents[3] / "chrome"
    if (dev / "manifest.json").exists():
        return dev
    return None


def _server_port() -> int:
    port_file = Path.home() / ".nexus" / "port"
    try:
        return int(port_file.read_text().strip())
    except (OSError, ValueError):
        return DEFAULT_PORT


def _get(url: str, headers: dict[str, str] | None = None, timeout: float = 3.0) -> dict | None:
    req = urllib.request.Request(url, headers=headers or {})
    for attempt in (0, 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return json.loads(res.read().decode())
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            if attempt:
                return None
            time.sleep(0.3)
    return None


def install(*, port: int | None = None, open_browser: bool = True) -> int:
    src = _source_dir()
    if src is None:
        print("error: extension sources not found (expected packaged chrome_ext/ or a dev checkout)")
        return 1
    version = json.loads((src / "manifest.json").read_text()).get("version", "?")
    if DEST_DIR.exists():
        shutil.rmtree(DEST_DIR)
    DEST_DIR.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, DEST_DIR)
    print(f"extension {version} installed at {DEST_DIR}")
    if not open_browser:
        return 0
    p = port or _server_port()
    url = f"http://localhost:{p}/ext"
    if _get(f"http://localhost:{p}/health"):
        print(f"opening {url}")
        webbrowser.open(url)
    else:
        print(f"server not reachable on port {p}; start it with `nexus daemon start`, then open {url}")
    return 0


def doctor(*, port: int | None = None) -> int:
    p = port or _server_port()
    checks: list[tuple[str, bool, str]] = []

    health = _get(f"http://localhost:{p}/health")
    checks.append(("server", bool(health), f"http://localhost:{p}"))

    manifest = DEST_DIR / "manifest.json"
    if manifest.exists():
        try:
            v = json.loads(manifest.read_text()).get("version", "?")
            checks.append(("extension files", True, f"{DEST_DIR} (v{v})"))
        except json.JSONDecodeError:
            checks.append(("extension files", False, "manifest.json unreadable"))
    else:
        checks.append(("extension files", False, f"missing at {DEST_DIR} — run `nexus chrome install`"))

    ping = _get(f"http://localhost:{p}/ext/ping")
    if ping and ping.get("extension_seen"):
        checks.append(
            ("extension connected", True, f"v{ping.get('version', '?')} pinged {ping.get('age_seconds')}s ago")
        )
    elif ping:
        checks.append(("extension connected", False, "no heartbeat yet — load it in Chrome via /ext"))
    else:
        checks.append(("extension connected", False, "server unreachable"))

    width = max(len(name) for name, _, _ in checks)
    failed = False
    for name, ok, detail in checks:
        mark = "✓" if ok else "✗"
        if not ok:
            failed = True
        print(f" {mark} {name.ljust(width)}  {detail}")
    return 1 if failed else 0
