"""Nexus CLI — config subcommand group."""

from __future__ import annotations

import sys

import typer

config_app = typer.Typer(help="Config file commands", no_args_is_help=True)


@config_app.command("path")
def config_path() -> None:
    """Print config file path."""
    from ..config_file import CONFIG_PATH
    typer.echo(str(CONFIG_PATH))


@config_app.command("show")
def config_show() -> None:
    """Dump current config as TOML."""
    from ..config_file import load, _cfg_to_dict
    import tomli_w
    cfg = load()
    sys.stdout.buffer.write(tomli_w.dumps(_cfg_to_dict(cfg)).encode())


@config_app.command("init")
def config_init() -> None:
    """Write default config to ~/.nexus/config.toml."""
    from ..config_file import CONFIG_PATH, default_config, save
    if CONFIG_PATH.exists():
        typer.echo(f"Config already exists at {CONFIG_PATH}. Delete it first to reinit.")
        raise typer.Exit(1)
    save(default_config())
    typer.echo(f"Config written to {CONFIG_PATH}")
