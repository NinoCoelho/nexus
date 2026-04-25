"""Nexus CLI — vault subcommand group."""

from __future__ import annotations

from typing import Optional

import typer

vault_app = typer.Typer(help="Vault commands", no_args_is_help=True)


@vault_app.command("ls")
def vault_ls(path: Optional[str] = typer.Argument(None)) -> None:
    """List vault files."""
    from ..vault import list_tree
    from rich.table import Table
    from rich.console import Console
    entries = list_tree()
    if path:
        entries = [e for e in entries if e.path.startswith(path.rstrip("/") + "/") or e.path == path]
    table = Table(title="Vault")
    table.add_column("Type")
    table.add_column("Path")
    table.add_column("Size", justify="right")
    for e in entries:
        size_str = str(e.size) if e.size is not None else ""
        table.add_row(e.type, e.path, size_str)
    Console().print(table)


@vault_app.command("search")
def vault_search(
    query: str = typer.Argument(...),
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """Full-text search across vault notes."""
    from ..vault_search import search, is_empty, rebuild_from_disk
    from rich.table import Table
    from rich.console import Console
    from rich.text import Text

    if is_empty():
        typer.echo("Index empty — rebuilding…")
        n = rebuild_from_disk()
        typer.echo(f"Indexed {n} files.")

    results = search(query, limit=limit)
    if not results:
        typer.echo("No results.")
        return

    table = Table(title=f'Search: "{query}"')
    table.add_column("Path", style="cyan", no_wrap=True)
    table.add_column("Snippet")
    for r in results:
        snippet = r["snippet"].replace("<mark>", "[bold yellow]").replace("</mark>", "[/bold yellow]")
        table.add_row(r["path"], Text.from_markup(snippet))
    Console().print(table)


@vault_app.command("reindex")
def vault_reindex() -> None:
    """Rebuild the full-text search index from disk."""
    from ..vault_search import rebuild_from_disk
    n = rebuild_from_disk()
    typer.echo(f"Indexed {n} files.")


@vault_app.command("tags")
def vault_tags() -> None:
    """List all tags in the vault with file counts."""
    from ..vault_index import list_tags, is_empty, rebuild_from_disk as rebuild_meta
    from rich.table import Table
    from rich.console import Console

    if is_empty():
        typer.echo("Tag index empty — rebuilding…")
        n = rebuild_meta()
        typer.echo(f"Indexed {n} files.")

    tags = list_tags()
    if not tags:
        typer.echo("No tags found.")
        return

    table = Table(title="Vault Tags")
    table.add_column("Tag", style="cyan")
    table.add_column("Files", justify="right")
    for t in tags:
        table.add_row(t["tag"], str(t["count"]))
    Console().print(table)


@vault_app.command("backlinks")
def vault_backlinks_cmd(path: str = typer.Argument(..., help="Relative vault file path")) -> None:
    """List files that link to a given vault file."""
    from ..vault_index import backlinks, is_empty, rebuild_from_disk as rebuild_meta
    from rich.console import Console

    if is_empty():
        typer.echo("Tag index empty — rebuilding…")
        rebuild_meta()

    links = backlinks(path)
    if not links:
        typer.echo(f"No backlinks to '{path}'.")
        return

    console = Console()
    console.print(f"[bold]Backlinks to[/bold] [cyan]{path}[/cyan]:")
    for link in links:
        console.print(f"  [dim]-[/dim] {link}")
