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


@config_app.command("edit")
def config_edit() -> None:
    """Open ~/.nexus/config.toml in $EDITOR (defaults to vi)."""
    import os
    import subprocess
    from ..config_file import CONFIG_PATH, default_config, load, save

    if not CONFIG_PATH.exists():
        save(default_config())
    editor = os.environ.get("EDITOR", "vi")
    subprocess.call([editor, str(CONFIG_PATH)])
    # Validate the result so the user notices a typo before the daemon restarts.
    try:
        load()
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"[warning] Config no longer parses: {exc}", err=True)
        raise typer.Exit(1)


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help='Dotted path, e.g. agent.default_model or search.enabled.'),
    value: str = typer.Argument(..., help='New value. Booleans, ints, and "null" are auto-coerced.'),
) -> None:
    """Set a single scalar config value via dotted path.

    Only scalar fields are supported; lists and providers/models tables
    keep their dedicated commands (``providers add``, ``models add``).
    """
    import json
    from pydantic import BaseModel
    from ..config_file import load, save

    cfg = load()
    parts = key.split(".")
    if not parts:
        typer.echo("Empty key.", err=True)
        raise typer.Exit(1)

    # Walk down to the parent model.
    parent: object = cfg
    for p in parts[:-1]:
        if not isinstance(parent, BaseModel) or p not in parent.__class__.model_fields:
            typer.echo(f"No such config path: {key}", err=True)
            raise typer.Exit(1)
        parent = getattr(parent, p)

    leaf = parts[-1]
    if not isinstance(parent, BaseModel) or leaf not in parent.__class__.model_fields:
        typer.echo(f"No such config field: {key}", err=True)
        raise typer.Exit(1)

    field_info = parent.__class__.model_fields[leaf]
    annotation = field_info.annotation

    # Best-effort scalar coercion.
    coerced: object = value
    if value.lower() == "null":
        coerced = None
    else:
        try:
            coerced = json.loads(value)
        except (TypeError, ValueError):
            coerced = value

    try:
        setattr(parent, leaf, coerced)
        # Re-validate the whole config so cross-field constraints fire.
        cfg.__class__.model_validate(cfg.model_dump())
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"Invalid value for {key} (expected {annotation}): {exc}", err=True)
        raise typer.Exit(1)

    save(cfg)
    typer.echo(f"{key} = {coerced!r}")
