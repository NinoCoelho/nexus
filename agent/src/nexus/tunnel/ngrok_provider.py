"""ngrok provider — thin wrapper over ``pyngrok``.

The ``pyngrok`` Python package is in pyproject deps, but the ngrok *binary*
itself is fetched separately on first use. We expose explicit helpers
(``binary_installed``, ``install_binary``) so the UI/CLI can pre-install with
clear feedback instead of relying on lazy download mid-activation.

Imports are local to each function so users that never touch the tunnel don't
pay the import cost.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


class NgrokError(RuntimeError):
    """Raised on any ngrok start/stop/install failure (auth, binary, config)."""


def _import_pyngrok():
    """Import pyngrok with a clear error if it isn't installed.

    Should never trip in production (it's a hard dep in pyproject.toml), but the
    error message points to the fix in case someone is running from a stale env.
    """
    try:
        from pyngrok import conf, ngrok, process
    except ImportError as e:  # pragma: no cover — install-time issue
        raise NgrokError(
            "pyngrok is not installed. Run `uv sync` from agent/ to install it.",
        ) from e
    return conf, ngrok, process


def _binary_path() -> Path:
    """Where pyngrok caches the ngrok binary (platform-default)."""
    conf, _ngrok, _process = _import_pyngrok()
    return Path(conf.get_default().ngrok_path)


def binary_installed() -> bool:
    """Return True if the ngrok binary exists on disk and looks executable."""
    try:
        p = _binary_path()
    except NgrokError:
        return False
    return p.is_file() and os.access(p, os.X_OK)


def install_binary() -> Path:
    """Idempotently download + install the ngrok binary. Returns the install path.

    Safe to call repeatedly; pyngrok skips the download when the binary is
    already present. Raises ``NgrokError`` with a readable message on failure
    (typically network / firewall issues).
    """
    conf, ngrok, _process = _import_pyngrok()
    cfg = conf.get_default()
    target = Path(cfg.ngrok_path)
    if target.is_file() and os.access(target, os.X_OK):
        log.info("ngrok binary already installed at %s", target)
        return target
    log.info("installing ngrok binary -> %s", target)
    try:
        ngrok.install_ngrok(pyngrok_config=cfg)
    except Exception as e:
        raise NgrokError(
            "Could not download the ngrok binary. Check your internet connection "
            f"or install it manually (https://ngrok.com/download). Underlying error: {e}",
        ) from e
    if not target.is_file():
        raise NgrokError(
            f"ngrok install reported success but no binary found at {target}.",
        )
    log.info("ngrok binary installed at %s", target)
    return target


def start_ngrok(*, authtoken: str, port: int, region: str = "us") -> str:
    """Start an HTTP tunnel pointing at ``http://localhost:{port}`` and return the public URL.

    Auto-installs the ngrok binary on first use so the user doesn't see a
    cryptic "ngrok not installed" failure mid-activation. Idempotent at the
    *process* level: if a tunnel for this port already exists, returns its URL
    instead of opening a second one (pyngrok semantics).
    """
    if not authtoken:
        raise NgrokError(
            "ngrok authtoken is required. Get one free at https://dashboard.ngrok.com "
            "and paste it into Settings → Sharing.",
        )
    conf, ngrok, _process = _import_pyngrok()

    # Make sure the binary is on disk before we configure auth + connect; the
    # download can be slow on a cold cache and we want it to succeed (or fail
    # cleanly with a good message) before we touch any tunnel state.
    install_binary()

    cfg = conf.PyngrokConfig(auth_token=authtoken, region=region)
    conf.set_default(cfg)
    try:
        tunnel = ngrok.connect(addr=str(port), proto="http", pyngrok_config=cfg)
    except Exception as e:  # pyngrok raises various provider errors
        raise NgrokError(f"ngrok start failed: {e}") from e
    url = tunnel.public_url
    if not url:
        raise NgrokError("ngrok returned an empty public_url")
    # Force https — ngrok serves both, but http leaks the token in headers.
    if url.startswith("http://"):
        url = "https://" + url[len("http://") :]
    log.info("ngrok tunnel up: %s -> http://localhost:%d", url, port)
    return url


def stop_ngrok() -> None:
    """Tear down all tunnels in this process. Best-effort — never raises.

    Note: ``get_tunnels`` will *spawn* ngrok if no process is running, which
    is the opposite of what we want during shutdown. We skip straight to
    ``kill`` if no process has been started in this session.
    """
    try:
        _conf, ngrok, process = _import_pyngrok()
    except NgrokError:
        return
    try:
        # process.get_processes() returns the dict of live processes keyed by
        # config_path. Empty → nothing to do.
        if not getattr(process, "_current_processes", {}):
            return
        for t in ngrok.get_tunnels():
            ngrok.disconnect(t.public_url)
        ngrok.kill()
    except Exception:
        log.exception("ngrok stop failed")


def resolve_authtoken(env_name: str = "NGROK_AUTHTOKEN") -> str:
    """Read the ngrok authtoken from the configured env var, returning '' if unset."""
    return os.environ.get(env_name, "").strip()
