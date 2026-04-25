"""Nexus CLI — sessions subcommand group."""

from __future__ import annotations

from typing import Optional

import typer

sessions_app = typer.Typer(help="Session commands", no_args_is_help=True)


@sessions_app.command("list")
def sessions_list() -> None:
    """List all sessions."""
    from ..server.session_store import SessionStore
    from rich.table import Table
    from rich.console import Console
    import datetime
    store = SessionStore()
    summaries = store.list(limit=50)
    table = Table(title="Sessions")
    table.add_column("ID", style="dim")
    table.add_column("Title")
    table.add_column("Messages", justify="right")
    table.add_column("Updated")
    for s in summaries:
        updated = datetime.datetime.fromtimestamp(s.updated_at).strftime("%Y-%m-%d %H:%M")
        table.add_row(s.id[:12], s.title, str(s.message_count), updated)
    Console().print(table)


@sessions_app.command("export")
def sessions_export(
    session_id: str = typer.Argument(...),
    out: Optional[str] = typer.Option(None, "--out", "-o", help="Output file path (default: stdout)"),
    port: int = typer.Option(18989, "--port"),
) -> None:
    """Export a session as markdown."""
    import httpx
    base = f"http://127.0.0.1:{port}"
    try:
        r = httpx.get(f"{base}/sessions/{session_id}/export", timeout=10)
        r.raise_for_status()
    except Exception as exc:
        typer.echo(f"[error] {exc}", err=True)
        raise typer.Exit(1)
    markdown = r.text
    if out:
        from pathlib import Path
        Path(out).write_text(markdown, encoding="utf-8")
        typer.echo(f"Exported to {out}")
    else:
        typer.echo(markdown)


@sessions_app.command("import")
def sessions_import(
    path: str = typer.Argument(..., help="Path to .md file"),
    port: int = typer.Option(18989, "--port"),
) -> None:
    """Import a session from a markdown file."""
    import httpx
    from pathlib import Path
    md = Path(path).read_text(encoding="utf-8")
    base = f"http://127.0.0.1:{port}"
    try:
        r = httpx.post(f"{base}/sessions/import", json={"markdown": md}, timeout=10)
        r.raise_for_status()
    except Exception as exc:
        typer.echo(f"[error] {exc}", err=True)
        raise typer.Exit(1)
    data = r.json()
    typer.echo(f"Imported session '{data['title']}' (id={data['id']}, messages={data['imported_message_count']})")


@sessions_app.command("show")
def sessions_show(session_id: str = typer.Argument(...)) -> None:
    """Show messages in a session."""
    from ..server.session_store import SessionStore
    from rich.table import Table
    from rich.console import Console
    store = SessionStore()
    session = store.get(session_id)
    if session is None:
        typer.echo(f"Session '{session_id}' not found.")
        raise typer.Exit(1)
    typer.echo(f"Session: {session.id}  Title: {session.title}")
    table = Table(title=f"Messages ({len(session.history)})")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Role")
    table.add_column("Content")
    for i, m in enumerate(session.history):
        content = (m.content or "")[:80]
        table.add_row(str(i), m.role, content)
    Console().print(table)
