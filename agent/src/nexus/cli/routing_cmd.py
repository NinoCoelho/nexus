"""Nexus CLI — routing subcommand group."""

from __future__ import annotations

import typer

routing_app = typer.Typer(help="Routing commands", no_args_is_help=True)


@routing_app.command("set")
def routing_set(mode: str = typer.Argument(..., help="fixed or auto")) -> None:
    """Set routing mode."""
    from ..config_file import load, save
    if mode not in ("fixed", "auto"):
        typer.echo("Mode must be 'fixed' or 'auto'.")
        raise typer.Exit(1)
    cfg = load()
    cfg.agent.routing_mode = mode
    save(cfg)
    typer.echo(f"Routing mode set to '{mode}'.")
