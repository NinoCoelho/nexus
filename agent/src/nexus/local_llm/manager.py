"""Runtime control of llama-server processes for local LLM inference.

Supports multiple concurrent servers — one per installed GGUF file. Each
running server gets its own port and is registered as ``providers.local-<slug>``
in ``~/.nexus/config.toml`` while the process is alive. State is module-level
so it persists across requests within a server process.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class ServerHandle:
    proc: subprocess.Popen
    port: int
    slug: str
    model_path: Path


# Keyed by GGUF filename (the basename, e.g. "Qwen2.5-7B-Instruct-Q4_K_M.gguf").
_servers: dict[str, ServerHandle] = {}


def slugify(name: str) -> str:
    """Convert a model stem to a lowercase slug suitable for config IDs."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower())
    return s.strip("-") or "model"


def is_running(filename: str) -> bool:
    """Return True iff a llama-server is currently up for this GGUF filename."""
    h = _servers.get(filename)
    if h is None:
        return False
    if h.proc.poll() is not None:
        # Process died — clean up.
        _servers.pop(filename, None)
        return False
    return True


def list_running() -> list[dict]:
    """Return a list of currently running servers."""
    out: list[dict] = []
    for filename in list(_servers.keys()):
        if not is_running(filename):
            continue
        h = _servers[filename]
        out.append({
            "filename": filename,
            "slug": h.slug,
            "port": h.port,
            "pid": h.proc.pid,
            "model_path": str(h.model_path),
        })
    return out


def discover_binary() -> Path | None:
    """Locate the llama-server binary.

    Search order:
    1. ``NEXUS_LLAMA_BIN`` environment variable.
    2. Bundled binary relative to ``NEXUS_BUNDLE_DIR`` (macOS .app).
    3. ``~/.nexus/llama/**/llama-server`` (user-installed binary).
    4. ``llama-server`` on ``PATH`` via ``shutil.which``.

    Returns:
        Path to the binary, or None if not found.
    """
    env_bin = os.environ.get("NEXUS_LLAMA_BIN", "")
    if env_bin:
        p = Path(env_bin)
        if p.is_file():
            return p

    bundle_dir = os.environ.get("NEXUS_BUNDLE_DIR", "")
    if bundle_dir:
        resources = Path(bundle_dir)
        for candidate in resources.glob("llama/**/llama-server"):
            if candidate.is_file():
                return candidate

    user_llama = Path.home() / ".nexus" / "llama"
    if user_llama.is_dir():
        for candidate in user_llama.glob("**/llama-server"):
            if candidate.is_file():
                return candidate

    which = shutil.which("llama-server")
    if which:
        return Path(which)

    return None


def start(model_path: Path, ctx_size: int = 16384) -> ServerHandle:
    """Start (or return existing) llama-server for the given model.

    If a server is already running for ``model_path.name``, returns its handle
    without spawning a duplicate.

    Raises:
        RuntimeError: If the binary is missing or the server fails to become
            ready within 90 seconds.
    """
    filename = model_path.name
    if is_running(filename):
        return _servers[filename]

    binary = discover_binary()
    if binary is None:
        raise RuntimeError(
            "llama-server binary not found. Set NEXUS_LLAMA_BIN, install "
            "llama.cpp, or place the binary under ~/.nexus/llama/.",
        )

    port = _pick_free_port()
    slug = slugify(model_path.stem)

    log_path = Path.home() / "Library" / "Logs" / "Nexus" / "llama-server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(log_path, "ab")

    cmd = [
        str(binary),
        "-m", str(model_path),
        "--host", "127.0.0.1",
        "--port", str(port),
        "-c", str(ctx_size),
        "-ngl", "99",
        "--jinja",
    ]

    log.info("[local_llm] starting llama-server: %s", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=log_handle, stderr=subprocess.STDOUT)

    health_url = f"http://127.0.0.1:{port}/v1/models"
    deadline = time.time() + 90.0
    import urllib.error
    import urllib.request

    while time.time() < deadline:
        if proc.poll() is not None:
            log.warning("[local_llm] llama-server exited early; see %s", log_path)
            raise RuntimeError(f"llama-server exited before becoming ready (see {log_path})")
        try:
            with urllib.request.urlopen(health_url, timeout=1.5) as r:
                if r.status == 200:
                    handle = ServerHandle(
                        proc=proc, port=port, slug=slug, model_path=model_path,
                    )
                    _servers[filename] = handle
                    log.info(
                        "[local_llm] llama-server ready: %s on :%d (slug=%s)",
                        filename, port, slug,
                    )
                    return handle
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)

    proc.terminate()
    raise RuntimeError(f"llama-server did not become ready in 90s (see {log_path})")


def stop(filename: str) -> bool:
    """Terminate the llama-server for the given GGUF filename.

    Returns:
        True if a server was running and stopped; False if nothing was up.
    """
    h = _servers.pop(filename, None)
    if h is None:
        return False
    try:
        h.proc.terminate()
        try:
            h.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            h.proc.kill()
            h.proc.wait()
    except Exception:
        log.exception("[local_llm] error stopping llama-server for %s", filename)
    return True


def stop_all() -> None:
    """Stop every running llama-server. Used during graceful shutdown."""
    for filename in list(_servers.keys()):
        stop(filename)


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------

def _provider_name(slug: str) -> str:
    return f"local-{slug}"


def _load_config() -> tuple[Path, dict]:
    import tomli_w  # noqa: F401  (used by callers via _save_config)
    try:
        import tomllib
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
    return cfg_path, raw


def _save_config(cfg_path: Path, raw: dict) -> None:
    import tomli_w
    with open(cfg_path, "wb") as f:
        tomli_w.dump(raw, f)


def add_to_config(slug: str, port: int) -> str:
    """Register a running local model in ``~/.nexus/config.toml``.

    Adds (or refreshes) ``providers.local-<slug>`` and a corresponding
    ``[[models]]`` entry. Preserves all other providers, models, and settings.

    Returns:
        The model id (``"local-<slug>/<slug>"``).
    """
    cfg_path, raw = _load_config()

    providers = raw.setdefault("providers", {})
    pname = _provider_name(slug)
    providers[pname] = {
        "base_url": f"http://127.0.0.1:{port}",
        "api_key_env": "",
        "use_inline_key": False,
        "type": "ollama",
    }

    # Drop legacy unified `local` provider from the singleton era — it was a
    # different model and would be misregistered now.
    providers.pop("local", None)

    model_id = f"{pname}/{slug}"
    models = list(raw.get("models", []))
    # Drop any prior entries for this exact slug; preserve unrelated local-* entries.
    models = [
        m for m in models
        if not (m.get("provider") == pname or m.get("id") == model_id)
    ]
    # Also clean up legacy ``provider == "local"`` entries from the singleton era.
    models = [m for m in models if m.get("provider") != "local"]
    models.append({
        "id": model_id,
        "provider": pname,
        "model_name": slug,
        "tags": ["local", "offline"],
        "tier": "fast",
        "notes": "Local GGUF model running via llama.cpp.",
    })
    raw["models"] = models

    agent_cfg = raw.setdefault("agent", {})
    cur_default = agent_cfg.get("default_model", "")
    # Only seed default_model if empty or pointing at a stale local-* model id
    # that's no longer in our models list.
    valid_ids = {m["id"] for m in models}
    if not cur_default or (
        cur_default.startswith(("local/", "local-")) and cur_default not in valid_ids
    ):
        agent_cfg["default_model"] = model_id

    _save_config(cfg_path, raw)
    log.info("[local_llm] added to config: %s @ :%d", pname, port)
    return model_id


def remove_from_config(slug: str) -> None:
    """Remove ``providers.local-<slug>`` and its corresponding model entry."""
    cfg_path, raw = _load_config()
    pname = _provider_name(slug)

    providers = raw.get("providers", {})
    providers.pop(pname, None)

    models = list(raw.get("models", []))
    raw["models"] = [m for m in models if m.get("provider") != pname]

    # If the stopped model was the default, clear it so the picker falls back.
    agent_cfg = raw.get("agent", {})
    cur_default = agent_cfg.get("default_model", "")
    if cur_default == f"{pname}/{slug}":
        agent_cfg["default_model"] = ""

    _save_config(cfg_path, raw)
    log.info("[local_llm] removed from config: %s", pname)


def cleanup_stale_config() -> None:
    """Drop config entries for local-* providers whose servers aren't running.

    Called at server startup to recover from a previous crash where the
    daemon died with running entries left in config.
    """
    cfg_path, raw = _load_config()
    providers = raw.get("providers", {})
    models = raw.get("models", [])

    running_pnames = {_provider_name(h.slug) for h in _servers.values()}

    changed = False
    for pname in list(providers.keys()):
        if pname == "local" or pname.startswith("local-"):
            if pname not in running_pnames:
                providers.pop(pname, None)
                changed = True

    new_models = [
        m for m in models
        if not (
            m.get("provider") == "local"
            or (
                isinstance(m.get("provider"), str)
                and m["provider"].startswith("local-")
                and m["provider"] not in running_pnames
            )
        )
    ]
    if len(new_models) != len(models):
        raw["models"] = new_models
        changed = True

    agent_cfg = raw.get("agent", {})
    cur_default = agent_cfg.get("default_model", "")
    valid_ids = {m["id"] for m in raw.get("models", [])}
    if cur_default and cur_default.startswith(("local/", "local-")) and cur_default not in valid_ids:
        agent_cfg["default_model"] = ""
        changed = True

    if changed:
        _save_config(cfg_path, raw)
        log.info("[local_llm] cleaned up stale local-* entries")


def _pick_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]
