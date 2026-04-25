"""Nexus CLI — skills subcommand group."""

from __future__ import annotations

import typer

skills_app = typer.Typer(help="Skills commands", no_args_is_help=True)


@skills_app.command("list")
def skills_list() -> None:
    """List skills."""
    from ..config import SKILLS_DIR
    from ..skills.registry import SkillRegistry
    from rich.table import Table
    from rich.console import Console
    reg = SkillRegistry(SKILLS_DIR)
    table = Table(title="Skills")
    table.add_column("Name")
    table.add_column("Description")
    table.add_column("Trust")
    for s in reg.list():
        table.add_row(s.name, s.description, s.trust)
    Console().print(table)


@skills_app.command("view")
def skills_view(name: str = typer.Argument(...)) -> None:
    """View a skill."""
    from ..config import SKILLS_DIR
    from ..skills.registry import SkillRegistry
    reg = SkillRegistry(SKILLS_DIR)
    try:
        s = reg.get(name)
    except KeyError:
        typer.echo(f"Skill '{name}' not found.")
        raise typer.Exit(1)
    typer.echo(s.body)


@skills_app.command("remove")
def skills_remove(name: str = typer.Argument(...)) -> None:
    """Remove a skill."""
    from ..config import SKILLS_DIR
    from ..skills.registry import SkillRegistry
    import shutil
    reg = SkillRegistry(SKILLS_DIR)
    try:
        reg.get(name)
    except KeyError:
        typer.echo(f"Skill '{name}' not found.")
        raise typer.Exit(1)
    skill_dir = SKILLS_DIR / name
    shutil.rmtree(skill_dir)
    typer.echo(f"Skill '{name}' removed.")
