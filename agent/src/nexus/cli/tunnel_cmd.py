"""Nexus CLI — tunnel subcommand group.

Out-of-band control of the public sharing tunnel. The state lives on the
running server (in :mod:`nexus.tunnel.manager`), so these commands talk to
it over loopback HTTP rather than mutating in-process state.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

import typer

tunnel_app = typer.Typer(
    help="Public sharing tunnel (Cloudflare Quick Tunnel)",
    no_args_is_help=True,
)


def _api_base() -> str:
    port = os.environ.get("NEXUS_PORT", "18989")
    return f"http://127.0.0.1:{port}"


def _request(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{_api_base()}{path}"
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url=url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8")).get("detail", "")
        except Exception:
            pass
        typer.echo(f"error: {e.code} {detail}", err=True)
        raise typer.Exit(code=1)
    except urllib.error.URLError:
        typer.echo(
            "error: could not reach the Nexus server. Is the daemon running? "
            "(`uv run nexus daemon start`)",
            err=True,
        )
        raise typer.Exit(code=1)


def _print_qr(text: str) -> None:
    """Render a QR code to stdout. Skips silently if `qrcode` isn't installed."""
    try:
        import qrcode  # type: ignore[import-untyped]
    except ImportError:
        return
    qr = qrcode.QRCode(border=1)
    qr.add_data(text)
    qr.make(fit=True)
    qr.print_ascii(out=sys.stdout, invert=True)


def _print_status(s: dict) -> None:
    if not s.get("active"):
        typer.echo("Tunnel: inactive")
        return
    typer.echo("Tunnel: active")
    typer.echo(f"  Public URL : {s.get('public_url')}")
    typer.echo(f"  Share link : {s.get('share_url')}")
    if s.get("code"):
        typer.echo(f"  Access code: {s.get('code')}")
    share_url = s.get("share_url") or ""
    if share_url:
        typer.echo("")
        _print_qr(share_url)


@tunnel_app.command("start")
def tunnel_start() -> None:
    """Open a public tunnel and print the share link + QR code."""
    s = _request("POST", "/tunnel/start")
    _print_status(s)


@tunnel_app.command("stop")
def tunnel_stop() -> None:
    """Tear down the active tunnel."""
    s = _request("POST", "/tunnel/stop")
    _print_status(s)


@tunnel_app.command("status")
def tunnel_status() -> None:
    """Show whether the tunnel is currently active."""
    s = _request("GET", "/tunnel/status")
    _print_status(s)


@tunnel_app.command("install")
def tunnel_install() -> None:
    """Download + install the cloudflared binary.

    Idempotent — safe to run repeatedly. The binary lands in ``~/.nexus/bin/``.
    Calling this ahead of ``nexus tunnel start`` lets you confirm the download
    works in isolation, but ``start`` also installs on demand if needed.
    """
    typer.echo("Installing cloudflared binary (this may take a moment)...")
    res = _request("POST", "/tunnel/install")
    typer.echo(f"cloudflared installed at {res.get('path')}")
