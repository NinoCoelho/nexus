"""Nexus CLI — pair command: native-app pairing QR.

Talks to the running server over loopback (same pattern as tunnel_cmd):
``GET /pair`` mints/returns the pairing descriptor, ``POST /pair/rotate``
revokes paired devices. Prints the deep-link QR for the native iOS client
plus the details for manual entry.
"""

from __future__ import annotations

import typer

from .tunnel_cmd import _print_qr, _request


def _print_payload(p: dict) -> None:
    typer.echo(f"  Machine : {p.get('name')}")
    typer.echo(f"  URL     : {p.get('url')}")
    typer.echo(f"  Token   : {p.get('token')}")
    features = p.get("features") or {}
    if features:
        flags = ", ".join(k for k, v in features.items() if v)
        typer.echo(f"  Features: {flags or '—'}")
    typer.echo("")
    typer.echo("Scan with the Nexus iOS app (or the iOS Camera app):")
    typer.echo("")
    _print_qr(str(p.get("deep_link", "")))


def pair(
    rotate: bool = typer.Option(False, "--rotate", help="Revoke paired devices by minting a fresh token"),
) -> None:
    """Show the QR code + credentials for pairing the native iOS app.

    Requires the Nexus server to be running (`nexus daemon start`). The
    phone must be on your tailnet (e.g. Tailscale) and the server bound to
    it: set `[server] bind = "tailscale"` in ~/.nexus/config.toml (or run
    `nexus serve --bind tailscale`), then restart.
    """
    if rotate:
        p = _request("POST", "/pair/rotate")
        typer.echo("Token rotated — previously paired devices were revoked.")
        typer.echo("")
        _print_payload(p)
        return
    p = _request("GET", "/pair")
    typer.echo("Pair a device:")
    typer.echo("")
    _print_payload(p)
