"""Nexus CLI — models subcommand group."""

from __future__ import annotations

import typer

models_app = typer.Typer(help="Model commands", no_args_is_help=True)


@models_app.command("list")
def models_list() -> None:
    """List configured models."""
    from ..config_file import load
    from rich.table import Table
    from rich.console import Console
    cfg = load()
    table = Table(title="Models")
    table.add_column("ID")
    table.add_column("Provider")
    table.add_column("Model Name")
    table.add_column("Tags")
    table.add_column("Tier")
    table.add_column("Notes")
    for m in cfg.models:
        table.add_row(m.id, m.provider, m.model_name, ",".join(m.tags), m.tier, m.notes or "")
    Console().print(table)


@models_app.command("add")
def models_add(
    id: str = typer.Argument(...),
    provider: str = typer.Option(..., "--provider"),
    model_name: str = typer.Option(..., "--model"),
    tags: str = typer.Option("", "--tags"),
    tier: str = typer.Option("", "--tier", help="fast|balanced|heavy (auto-detected if omitted)"),
    notes: str = typer.Option("", "--notes"),
) -> None:
    """Add a model."""
    from ..config_file import load, save, ModelEntry
    from ..agent.model_profiles import suggest_tier
    cfg = load()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    resolved_tier = tier.strip() or suggest_tier(model_name)
    if resolved_tier not in ("fast", "balanced", "heavy"):
        raise typer.BadParameter("tier must be fast|balanced|heavy")
    m = ModelEntry(
        id=id,
        provider=provider,
        model_name=model_name,
        tags=tag_list,
        tier=resolved_tier,  # type: ignore[arg-type]
        notes=notes,
    )
    cfg.models.append(m)
    save(cfg)
    typer.echo(f"Model '{id}' added (tier={resolved_tier}).")


@models_app.command("remove")
def models_remove(id: str = typer.Argument(...)) -> None:
    """Remove a model."""
    from ..config_file import load, save
    cfg = load()
    before = len(cfg.models)
    cfg.models = [m for m in cfg.models if m.id != id]
    if len(cfg.models) == before:
        typer.echo(f"Model '{id}' not found.")
        raise typer.Exit(1)
    save(cfg)
    typer.echo(f"Model '{id}' removed.")


@models_app.command("update")
def models_update(
    id: str = typer.Argument(...),
    model_name: str = typer.Option(None, "--model"),
    tags: str = typer.Option(None, "--tags", help="Comma-separated, replaces existing tags."),
    tier: str = typer.Option(None, "--tier", help="fast|balanced|heavy"),
    notes: str = typer.Option(None, "--notes"),
    context_window: int = typer.Option(None, "--context-window"),
    max_output_tokens: int = typer.Option(None, "--max-output-tokens"),
) -> None:
    """Update fields on an existing model entry. id and provider are immutable."""
    from ..config_file import load, save

    cfg = load()
    for i, m in enumerate(cfg.models):
        if m.id != id:
            continue
        updates: dict[str, object] = {}
        if model_name is not None:
            updates["model_name"] = model_name
        if tags is not None:
            updates["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
        if tier is not None:
            if tier not in ("fast", "balanced", "heavy"):
                raise typer.BadParameter("tier must be fast|balanced|heavy")
            updates["tier"] = tier
        if notes is not None:
            updates["notes"] = notes
        if context_window is not None:
            updates["context_window"] = context_window
        if max_output_tokens is not None:
            if max_output_tokens < 0:
                raise typer.BadParameter("--max-output-tokens must be >= 0")
            updates["max_output_tokens"] = max_output_tokens
        if not updates:
            typer.echo("Nothing to update.")
            return
        cfg.models[i] = m.model_copy(update=updates)
        save(cfg)
        typer.echo(f"Model '{id}' updated: {', '.join(updates)}")
        return
    typer.echo(f"Model '{id}' not found.")
    raise typer.Exit(1)


@models_app.command("set-default")
def models_set_default(id: str = typer.Argument(...)) -> None:
    """Set the default model."""
    from ..config_file import load, save
    cfg = load()
    ids = [m.id for m in cfg.models]
    if id not in ids:
        typer.echo(f"Model '{id}' not found. Available: {ids}")
        raise typer.Exit(1)
    cfg.agent.default_model = id
    save(cfg)
    typer.echo(f"Default model set to '{id}'.")
