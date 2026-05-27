from __future__ import annotations

import typer

app = typer.Typer(help="User and access management")


@app.command("list")
def list_users() -> None:
    """List all registered users."""
    from ..server.user_store import UserStore

    store = UserStore()
    users = store.list_users()
    if not users:
        typer.echo("No users registered.")
        return
    for u in users:
        status = f" ({u.status})" if u.status != "active" else ""
        typer.echo(f"  {u.email}  [{u.role}]{status}  ({u.display_name})")
    store.close()


@app.command("invite")
def create_invite(
    role: str = typer.Option("member", help="Role for the new user (admin/member/viewer)"),
    email: str = typer.Option("", help="Restrict to this email"),
    max_uses: int = typer.Option(1, help="How many times the invite can be used"),
) -> None:
    """Create an invite code for a new user.

    Requires at least one admin to exist. The generated code can be shared
    with the new user who then registers at /invite/<code>.
    """
    from ..server.user_store import UserStore

    store = UserStore()
    users = store.list_users()
    if not users:
        typer.echo("No users exist yet. Sign in with a Nexus account to create the first admin.", err=True)
        raise typer.Exit(1)

    admin = next((u for u in users if u.role == "admin"), None)
    if admin is None:
        typer.echo("No admin user found.", err=True)
        raise typer.Exit(1)

    invite = store.create_invite(
        created_by=admin.id,
        email=email or None,
        role=role,
        max_uses=max_uses,
    )
    typer.echo(f"Invite code: {invite.code}")
    typer.echo(f"  Role: {invite.role}")
    typer.echo(f"  Max uses: {invite.max_uses}")
    if invite.email:
        typer.echo(f"  Restricted to: {invite.email}")
    typer.echo(f"  Share URL: /invite/{invite.code}")
    store.close()
