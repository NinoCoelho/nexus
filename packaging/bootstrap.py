"""Launcher executed by the macOS bundle to start the Nexus FastAPI server.

The Swift host app spawns this with the bundled standalone Python interpreter.
Layout assumed at runtime (relative to this file):

    bootstrap.py
    site-packages/        # full venv contents
    ui/                   # ui/dist contents (index.html at root)
    models/
        fastembed/
        ...

The Swift app passes the chosen TCP port via NEXUS_PORT (or we pick one).
Readiness is signalled by ``GET /health`` returning 200.
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path


def _pick_port(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    here = Path(__file__).resolve().parent
    site = here / "site-packages"
    if site.is_dir():
        sys.path.insert(0, str(site))

    models_dir = here / "models"
    if models_dir.is_dir():
        os.environ.setdefault("NEXUS_MODELS_DIR", str(models_dir))
        os.environ.setdefault("HF_HOME", str(models_dir / "huggingface"))
        os.environ.setdefault("XDG_CACHE_HOME", str(models_dir / "cache"))

    ui_dist = here / "ui"
    if (ui_dist / "index.html").is_file():
        os.environ.setdefault("NEXUS_UI_DIST", str(ui_dist))

    port = _pick_port(int(os.environ.get("NEXUS_PORT", "18989")))
    # Write the chosen port so the Swift app can read it (avoids parsing logs).
    port_file = Path(os.environ.get("NEXUS_PORT_FILE", here / ".port"))
    try:
        port_file.write_text(str(port))
    except OSError:
        pass

    import uvicorn  # type: ignore

    uvicorn.run(
        "nexus.main:app",
        host="127.0.0.1",
        port=port,
        log_level=os.environ.get("NEXUS_LOG_LEVEL", "info"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
