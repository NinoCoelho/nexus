from __future__ import annotations

import typer

app = typer.Typer(help="User and access management")


@app.command("admin-token")
def admin_token() -> None:
    """Generate a new one-time admin setup token.

    Use this when you need to re-create the initial admin account (e.g. the
    account was accidentally deleted or the token expired). The token is
    consumed after a single successful use.

    Only works when the server is running and multi-user mode is enabled
    with no active users. If users already exist, create invites instead
    via the admin panel.
    """
    from ..server.routes.auth import generate_bootstrap_token, get_bootstrap_token

    existing = get_bootstrap_token()
    if existing:
        typer.echo(f"Existing unused token: {existing}")
        typer.echo("Generating a new one (old token invalidated)...")
    token = generate_bootstrap_token()
    typer.echo(f"Setup token: {token}")
    typer.echo("")
    typer.echo("Open the app and enter this token on the setup page,")
    typer.echo("or if the server is running on loopback, the setup page")
    typer.echo("will open automatically without requiring the token.")


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
        typer.echo("No users exist yet. Use 'admin-token' to set up the first admin.", err=True)
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
