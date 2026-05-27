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
    import os
    import urllib.request
    import urllib.error
    import json

    port = os.environ.get("NEXUS_PORT", "18989")
    url = f"http://127.0.0.1:{port}/auth/generate-bootstrap-token"

    try:
        req = urllib.request.Request(url, method="POST", data=b"", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()
        except Exception:
            pass
        if e.code == 403:
            typer.echo("Error: endpoint is loopback-only.", err=True)
        elif e.code == 400:
            typer.echo(f"Error: {body or 'users already exist'}", err=True)
        else:
            typer.echo(f"Error: HTTP {e.code} — {body}", err=True)
        raise typer.Exit(1)
    except urllib.error.URLError:
        typer.echo("Error: server is not running on "
                   f"http://127.0.0.1:{port}. Start it first with `nexus serve` or `nexus daemon start`.", err=True)
        raise typer.Exit(1)

    token = data["token"]
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
