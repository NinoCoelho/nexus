"""Launcher executed by the macOS bundle to start the Nexus FastAPI server.

The Swift host app spawns this with the bundled standalone Python interpreter.
Layout assumed at runtime (relative to this file):

    bootstrap.py
    site-packages/        # full venv contents
    ui/                   # ui/dist contents (index.html at root)
    models/
        fastembed/
        spacy/en_core_web_sm_pkg/...
        llm/<name>.gguf   # bundled local LLM weights (optional)
    llama/.../llama-server  # llama.cpp server binary (optional)
    llm.json              # manifest describing the bundled LLM (optional)

The chosen TCP port is written to NEXUS_PORT_FILE so the Swift app can read
it without parsing logs. Readiness is signalled by ``GET /health`` → 200.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

NEXUS_HOME = Path.home() / ".nexus"
HOST_FILE = NEXUS_HOME / "host.json"
TOKEN_FILE = NEXUS_HOME / "access_token"

log = logging.getLogger("nexus.bootstrap")


def _pick_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _load_host_settings() -> tuple[str, int | None]:
    """Read user's bind preferences from ~/.nexus/host.json.

    Schema: {"host": "127.0.0.1", "port": 0}  — port 0 means "auto-pick free".
    Defaults to loopback + auto if file missing or unreadable.
    """
    if not HOST_FILE.is_file():
        return "127.0.0.1", None
    try:
        d = json.loads(HOST_FILE.read_text())
        host = str(d.get("host") or "127.0.0.1")
        port = int(d.get("port") or 0) or None
        return host, port
    except (OSError, ValueError):
        return "127.0.0.1", None


def _ensure_access_token() -> str:
    """Read or create a persistent random token in ~/.nexus/access_token."""
    NEXUS_HOME.mkdir(parents=True, exist_ok=True)
    if TOKEN_FILE.is_file():
        tok = TOKEN_FILE.read_text().strip()
        if tok:
            return tok
    tok = secrets.token_urlsafe(32)
    TOKEN_FILE.write_text(tok)
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass
    return tok


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


def _start_llama(here: Path) -> tuple[subprocess.Popen | None, int | None, str | None]:
    """Launch the bundled llama.cpp server if a manifest is present.

    Returns (process, port, model_name) on success; (None, None, None) otherwise.
    """
    manifest = here / "llm.json"
    if not manifest.is_file():
        return None, None, None
    try:
        cfg = json.loads(manifest.read_text())
    except (OSError, ValueError):
        return None, None, None

    binary = here / cfg["binary"]
    model = here / cfg["model_file"]
    if not binary.is_file() or not model.is_file():
        log.warning("[bootstrap] llm.json present but binary or model missing")
        return None, None, None

    port = _pick_free_port()
    log_path = Path.home() / "Library" / "Logs" / "Nexus" / "llama-server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(log_path, "ab")

    cmd = [
        str(binary), "-m", str(model),
        "--host", "127.0.0.1", "--port", str(port),
        "-c", str(int(cfg.get("ctx_size", 16384))),
        "-ngl", "99",     # offload all layers to Metal
        "--jinja",        # use the GGUF's embedded chat template — required
                          # for proper tool-call formatting on Qwen / Hermes.
                          # Without this, llama-server falls back to a generic
                          # parser that won't emit OpenAI tool_calls and the
                          # model just narrates "I'll use the tool".
    ]
    proc = subprocess.Popen(cmd, stdout=log_handle, stderr=subprocess.STDOUT)

    deadline = time.time() + 90
    health_url = f"http://127.0.0.1:{port}/v1/models"
    while time.time() < deadline:
        if proc.poll() is not None:
            log.warning("[bootstrap] llama-server exited early; see %s", log_path)
            return None, None, None
        try:
            with urllib.request.urlopen(health_url, timeout=1.5) as r:
                if r.status == 200:
                    return proc, port, cfg["model_name"]
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)

    log.warning("[bootstrap] llama-server did not become ready in time")
    proc.terminate()
    return None, None, None


def _seed_local_llm_config(model_name: str, llama_port: int) -> None:
    """Add/update the `local` provider + bundled model entry in ~/.nexus/config.toml.

    Rewrites the file each launch because the llama-server port is dynamic.
    Preserves all other providers, models, and settings; only sets
    ``agent.default_model`` if it was empty.
    """
    import tomli_w
    try:
        import tomllib  # py3.11+
    except ImportError:  # pragma: no cover
        import tomli as tomllib  # type: ignore

    cfg_path = Path.home() / ".nexus" / "config.toml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    raw: dict = {}
    if cfg_path.is_file():
        try:
            with open(cfg_path, "rb") as f:
                raw = tomllib.load(f)
        except (OSError, ValueError):
            raw = {}

    providers = raw.setdefault("providers", {})
    # Use type="ollama" so registry.py treats this as anonymous (no API key
    # required). The registry appends "/v1" itself, so base_url omits it.
    # llama.cpp's server accepts the same OpenAI-compatible endpoints Ollama
    # exposes, so the OpenAIProvider works transparently against either.
    providers["local"] = {
        "base_url": f"http://127.0.0.1:{llama_port}",
        "api_key_env": "",
        "use_inline_key": False,
        "type": "ollama",
    }

    model_id = f"local/{model_name}"
    # Drop any stale local/* model entries (e.g. from a previous bundle that
    # shipped a different model) before adding the current one.
    models = [m for m in raw.get("models", []) if m.get("provider") != "local"]
    models.append({
        "id": model_id,
        "provider": "local",
        "model_name": model_name,
        "tags": ["local", "bundled", "offline"],
        "tier": "fast",
        "notes": "Bundled with Nexus.app — runs locally via llama.cpp.",
    })
    raw["models"] = models

    agent = raw.setdefault("agent", {})
    # Repoint default_model if it pointed at a stale bundled local model
    # (e.g. user upgraded the .app to a bundle with a different model).
    cur_default = agent.get("default_model", "")
    if not cur_default or (cur_default.startswith("local/") and cur_default != model_id):
        agent["default_model"] = model_id

    with open(cfg_path, "wb") as f:
        tomli_w.dump(raw, f)


def _strip_local_llm_config() -> None:
    """Remove the bundled `local` provider + any local/* model rows.

    Runs when bootstrap finds no bundled LLM at startup but the user's config
    still has the entries seeded by a previous bundle. Leaves all other
    providers, models, and settings untouched. Clears agent.default_model
    only if it pointed at the stripped local model.
    """
    import tomli_w
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        import tomli as tomllib  # type: ignore

    cfg_path = Path.home() / ".nexus" / "config.toml"
    if not cfg_path.is_file():
        return
    try:
        with open(cfg_path, "rb") as f:
            raw = tomllib.load(f)
    except (OSError, ValueError):
        return

    changed = False
    providers = raw.get("providers", {})
    if "local" in providers:
        del providers["local"]
        changed = True

    before = len(raw.get("models", []))
    models = [m for m in raw.get("models", []) if m.get("provider") != "local"]
    if len(models) != before:
        raw["models"] = models
        changed = True

    agent = raw.get("agent", {})
    if str(agent.get("default_model", "")).startswith("local/"):
        agent["default_model"] = ""
        changed = True

    if changed:
        with open(cfg_path, "wb") as f:
            tomli_w.dump(raw, f)


def _stop_proc(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    except OSError:
        pass


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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

    llama_proc, llama_port, llama_model = _start_llama(here)
    if llama_proc is not None:
        atexit.register(_stop_proc, llama_proc)
        try:
            _seed_local_llm_config(llama_model, llama_port)
            log.info("[bootstrap] local LLM ready: %s on :%d", llama_model, llama_port)
        except Exception as e:  # noqa: BLE001
            log.warning("[bootstrap] could not seed local LLM config: %s", e)
    else:
        # No bundled LLM (or it failed to start). Strip any stale entries left
        # in the user's config from a previous bundle that did ship one.
        try:
            _strip_local_llm_config()
        except Exception as e:  # noqa: BLE001
            log.warning("[bootstrap] could not strip stale local LLM config: %s", e)

    bind_host, requested_port = _load_host_settings()
    # uvicorn binds to whatever host the user picked; for the port-probe socket
    # we use loopback so we don't accidentally fail on a hostname that the OS
    # only binds for incoming traffic.
    port = requested_port if requested_port else _pick_free_port()
    token = _ensure_access_token()
    os.environ["NEXUS_ACCESS_TOKEN"] = token
    port_file = Path(os.environ.get("NEXUS_PORT_FILE", here / ".port"))
    try:
        port_file.write_text(str(port))
    except OSError:
        pass
    # Write a sibling file with the bind host so the Swift host can show the
    # right URL in its menu (otherwise it would assume 127.0.0.1).
    try:
        (port_file.parent / ".host").write_text(bind_host)
    except OSError:
        pass

    log.info("[bootstrap] uvicorn binding %s:%d (auth %s)",
             bind_host, port, "required for non-loopback" if token else "disabled")

    import uvicorn  # type: ignore

    uvicorn.run(
        "nexus.main:app",
        host=bind_host,
        port=port,
        log_level=os.environ.get("NEXUS_LOG_LEVEL", "info"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
