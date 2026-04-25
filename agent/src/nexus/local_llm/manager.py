"""Runtime control of the llama-server process for local LLM inference.

Manages a single llama-server subprocess. State is stored in module-level
variables so it persists across requests within a server process.
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

_proc: subprocess.Popen | None = None
_port: int | None = None
_model_name: str | None = None
_model_path: Path | None = None


def current() -> dict | None:
    """Return info about the currently running llama-server, or None."""
    if _proc is None or _proc.poll() is not None:
        return None
    return {
        "model_name": _model_name,
        "port": _port,
        "model_path": str(_model_path) if _model_path else None,
        "pid": _proc.pid,
    }


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


def start(model_path: Path, model_name: str, ctx_size: int = 16384) -> tuple[int, str]:
    """Start llama-server with the given model.

    Args:
        model_path: Absolute path to the GGUF file.
        model_name: Logical name used in config (e.g. ``"qwen2.5-7b"``).
        ctx_size: Context window size in tokens.

    Returns:
        Tuple of ``(port, model_name)``.

    Raises:
        RuntimeError: If the binary is not found or the server does not
            become ready within 90 seconds.
    """
    global _proc, _port, _model_name, _model_path

    binary = discover_binary()
    if binary is None:
        raise RuntimeError(
            "llama-server binary not found. Set NEXUS_LLAMA_BIN or install llama.cpp."
        )

    port = _pick_free_port()

    log_path = Path.home() / "Library" / "Logs" / "Nexus" / "llama-server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(log_path, "ab")  # noqa: WPS515

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
                    _proc = proc
                    _port = port
                    _model_name = model_name
                    _model_path = model_path
                    log.info("[local_llm] llama-server ready on port %d", port)
                    return port, model_name
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)

    proc.terminate()
    raise RuntimeError(f"llama-server did not become ready in 90s (see {log_path})")


def stop() -> None:
    """Terminate the running llama-server process, if any."""
    global _proc, _port, _model_name, _model_path

    if _proc is None:
        return
    try:
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _proc.kill()
            _proc.wait()
    except Exception:
        log.exception("[local_llm] error stopping llama-server")
    finally:
        _proc = None
        _port = None
        _model_name = None
        _model_path = None


def restart(model_path: Path, model_name: str, ctx_size: int = 16384) -> tuple[int, str]:
    """Stop any running llama-server and start a new one.

    Args:
        model_path: Absolute path to the GGUF file.
        model_name: Logical name for config.
        ctx_size: Context window size.

    Returns:
        Tuple of ``(port, model_name)``.
    """
    stop()
    return start(model_path, model_name, ctx_size)


def seed_config(model_name: str, port: int) -> None:
    """Write/update the ``local`` provider and model entry in ``~/.nexus/config.toml``.

    Mirrors the logic from ``packaging/bootstrap.py:_seed_local_llm_config``.
    Preserves all existing providers, models, and settings. Only sets
    ``agent.default_model`` if it was empty or pointed at a stale local model.

    Args:
        model_name: Model name string (e.g. ``"qwen2.5-7b"``).
        port: The port llama-server is listening on.
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
    providers["local"] = {
        "base_url": f"http://127.0.0.1:{port}",
        "api_key_env": "",
        "use_inline_key": False,
        "type": "ollama",
    }

    model_id = f"local/{model_name}"
    # Drop any stale local/* model entries before adding the current one.
    models = [m for m in raw.get("models", []) if m.get("provider") != "local"]
    models.append({
        "id": model_id,
        "provider": "local",
        "model_name": model_name,
        "tags": ["local", "offline"],
        "tier": "fast",
        "notes": "Local GGUF model running via llama.cpp.",
    })
    raw["models"] = models

    agent_cfg = raw.setdefault("agent", {})
    cur_default = agent_cfg.get("default_model", "")
    if not cur_default or (cur_default.startswith("local/") and cur_default != model_id):
        agent_cfg["default_model"] = model_id

    with open(cfg_path, "wb") as f:
        tomli_w.dump(raw, f)

    log.info("[local_llm] seeded config: provider=local model=%s port=%d", model_name, port)


def _pick_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]
