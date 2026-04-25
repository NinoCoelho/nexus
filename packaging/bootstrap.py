"""Launcher executed by the macOS bundle to start the Nexus FastAPI server.

The Swift host app spawns this with the bundled standalone Python interpreter.
Layout assumed at runtime (relative to this file):

    bootstrap.py
    site-packages/        # full venv contents
    ui/                   # ui/dist contents (index.html at root)
    models/
        fastembed/
        spacy/en_core_web_sm_pkg/...

The chosen TCP port is written to NEXUS_PORT_FILE so the Swift app can read
it without parsing logs. Readiness is signalled by ``GET /health`` → 200.
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
from pathlib import Path


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed_spacy_cache(bundled_models: Path) -> None:
    """Copy the bundled spaCy model into ~/.nexus/models/spacy/en_core_web_sm.

    The builtin extractor checks that path first before falling back to
    `spacy.load("en_core_web_sm")` (which would trigger a network download).
    """
    cache = Path.home() / ".nexus" / "models" / "spacy" / "en_core_web_sm"
    if cache.is_dir():
        return
    bundled = bundled_models / "spacy" / "en_core_web_sm_pkg"
    if not bundled.is_dir():
        return
    meta = next((p for p in bundled.rglob("meta.json")), None)
    if meta is None:
        return
    cache.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(meta.parent, cache)


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
        _seed_spacy_cache(models_dir)

    ui_dist = here / "ui"
    if (ui_dist / "index.html").is_file():
        os.environ.setdefault("NEXUS_UI_DIST", str(ui_dist))

    port = _pick_free_port()
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
