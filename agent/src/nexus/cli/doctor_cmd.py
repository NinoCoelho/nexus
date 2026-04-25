"""Nexus CLI — version and doctor commands (top-level)."""

from __future__ import annotations

import os
from importlib import metadata
from pathlib import Path

import typer


def version() -> None:
    """Print the Nexus package version."""
    try:
        v = metadata.version("nexus")
    except metadata.PackageNotFoundError:
        v = "unknown"
    typer.echo(f"nexus {v}")


def doctor(
    port: int = typer.Option(18989, "--port", "-p", help="Port the daemon should be listening on."),
) -> None:
    """Run a quick health check of the local install.

    Verifies config exists, providers have keys, models are registered,
    and the daemon is reachable on the given port.
    """
    import httpx

    from ..config import SKILLS_DIR
    from ..config_file import CONFIG_PATH, load
    from ..server.session_store.store import _DB_PATH

    rows: list[tuple[str, str, str]] = []

    def _ok(label: str, detail: str = "") -> None:
        rows.append(("✓", label, detail))

    def _warn(label: str, detail: str = "") -> None:
        rows.append(("!", label, detail))

    def _fail(label: str, detail: str = "") -> None:
        rows.append(("✗", label, detail))

    # Config file
    if CONFIG_PATH.exists():
        _ok("config.toml", str(CONFIG_PATH))
    else:
        _fail("config.toml", f"missing at {CONFIG_PATH} — run `nexus config init`")

    # Load and inspect
    try:
        cfg = load()
    except Exception as exc:  # noqa: BLE001
        _fail("config parse", str(exc))
        cfg = None

    if cfg is not None:
        # Providers — does at least one have a usable key?
        ready = []
        for name, p in cfg.providers.items():
            if p.type == "ollama":
                ready.append(f"{name} (anonymous)")
            elif p.api_key_env and os.environ.get(p.api_key_env):
                ready.append(f"{name} (env)")
            elif p.use_inline_key:
                from ..secrets import get as _secret_get
                if _secret_get(name):
                    ready.append(f"{name} (inline)")
        if ready:
            _ok(f"providers ({len(ready)})", ", ".join(ready))
        else:
            _warn("providers", "no provider has a usable key — run `nexus providers set-key <name>` or set the env var")

        # Models
        if cfg.models:
            _ok(f"models ({len(cfg.models)})", ", ".join(m.id for m in cfg.models[:4]))
        else:
            _warn("models", "no models registered — run `nexus models add` or use the UI")

        # Default model
        if cfg.agent.default_model:
            _ok("default model", cfg.agent.default_model)
        else:
            _warn("default model", "not set — agent will fall back to the first registered model")

    # State files
    if Path(_DB_PATH).expanduser().exists():
        _ok("sessions.sqlite", str(_DB_PATH))
    else:
        _warn("sessions.sqlite", "not created yet (no sessions stored)")

    if SKILLS_DIR.exists():
        n = sum(1 for _ in SKILLS_DIR.iterdir() if (_ / "SKILL.md").is_file())
        _ok(f"skills ({n})", str(SKILLS_DIR))
    else:
        _warn("skills dir", f"missing at {SKILLS_DIR}")

    # Daemon reachability
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.5)
        r.raise_for_status()
        _ok("daemon", f"127.0.0.1:{port}")
    except Exception:
        _warn("daemon", f"not reachable on port {port} — `nexus daemon start` (or pass --port)")

    # Render
    from rich.console import Console
    from rich.table import Table

    table = Table(show_header=False, box=None)
    table.add_column(style="bold")
    table.add_column()
    table.add_column(style="dim")
    for status, label, detail in rows:
        style = {"✓": "green", "!": "yellow", "✗": "red"}[status]
        table.add_row(f"[{style}]{status}[/{style}]", label, detail)
    Console().print(table)

    if any(s == "✗" for s, _, _ in rows):
        raise typer.Exit(1)
