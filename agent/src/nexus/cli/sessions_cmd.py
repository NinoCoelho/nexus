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
@sessions_app.command("view")
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


@sessions_app.command("delete")
def sessions_delete(
    session_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Delete a session and all its messages."""
    from ..server.session_store import SessionStore
    store = SessionStore()
    session = store.get(session_id)
    if session is None:
        typer.echo(f"Session '{session_id}' not found.")
        raise typer.Exit(1)
    if not yes:
        typer.confirm(
            f"Delete session {session_id[:12]} ({session.title!r}) — {len(session.history)} messages?",
            abort=True,
        )
    store.delete(session_id)
    typer.echo(f"Deleted session {session_id[:12]}.")


@sessions_app.command("truncate")
def sessions_truncate(
    session_id: str = typer.Argument(...),
    before: int = typer.Option(..., "--before", "-b", help="Drop everything from this seq onward (0-indexed)."),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Trim a session's history, keeping only messages before <seq>."""
    from ..server.session_store import SessionStore
    store = SessionStore()
    session = store.get(session_id)
    if session is None:
        typer.echo(f"Session '{session_id}' not found.")
        raise typer.Exit(1)
    if before < 0 or before > len(session.history):
        typer.echo(f"--before must be in [0, {len(session.history)}].")
        raise typer.Exit(1)
    dropped = len(session.history) - before
    if dropped == 0:
        typer.echo("Nothing to drop.")
        return
    if not yes:
        typer.confirm(f"Drop {dropped} message(s) from session {session_id[:12]}?", abort=True)
    store.replace_history(session_id, session.history[:before])
    typer.echo(f"Truncated to {before} message(s).")


@sessions_app.command("share")
def sessions_share(
    session_id: str = typer.Argument(...),
    origin: str = typer.Option(
        "http://127.0.0.1:18989", "--origin",
        help="Origin used to build the shareable URL.",
    ),
) -> None:
    """Mint (or rotate) a read-only share link for a session.

    The token is HMAC-signed and stored in the local DB; ``unshare``
    revokes it.
    """
    import secrets as _secrets
    from ..server.session_store import SessionStore
    from ..server.routes.share import _ensure_share_table, _sign

    store = SessionStore()
    if store.get(session_id) is None:
        typer.echo(f"Session '{session_id}' not found.")
        raise typer.Exit(1)
    _ensure_share_table(store)
    nonce = _secrets.token_urlsafe(12)
    store._loom._db.execute(
        "INSERT INTO session_share (session_id, nonce) VALUES (?, ?) "
        "ON CONFLICT(session_id) DO UPDATE SET nonce = excluded.nonce, "
        "created_at = CURRENT_TIMESTAMP",
        (session_id, nonce),
    )
    store._loom._db.commit()
    sig = _sign(session_id, nonce)
    token = f"{session_id}.{nonce}.{sig}"
    typer.echo(f"{origin.rstrip('/')}/#/share/{token}")


@sessions_app.command("unshare")
def sessions_unshare(session_id: str = typer.Argument(...)) -> None:
    """Revoke a session's share link, if any."""
    from ..server.session_store import SessionStore
    from ..server.routes.share import _ensure_share_table

    store = SessionStore()
    _ensure_share_table(store)
    cur = store._loom._db.execute(
        "DELETE FROM session_share WHERE session_id = ?", (session_id,)
    )
    store._loom._db.commit()
    if cur.rowcount:
        typer.echo(f"Revoked share link for {session_id[:12]}.")
    else:
        typer.echo("No share link to revoke.")
