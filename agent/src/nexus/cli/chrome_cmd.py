"""Nexus CLI — chrome subcommand group (side-panel extension setup)."""

from __future__ import annotations

import typer

chrome_app = typer.Typer(help="Chrome side-panel extension setup", no_args_is_help=True)


@chrome_app.command("install")
def chrome_install(
    port: int = typer.Option(None, "--port", "-p", help="Server port (default: auto-detect, else 18989)"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open the guided install page"),
) -> None:
    """Copy the bundled extension to ~/.nexus/chrome-extension and open the
    guided install page (load-unpacked steps + connection check)."""
    from .. import chrome_install as impl

    raise typer.Exit(impl.install(port=port, open_browser=not no_browser))


@chrome_app.command("doctor")
def chrome_doctor(
    port: int = typer.Option(None, "--port", "-p", help="Server port (default: auto-detect, else 18989)"),
) -> None:
    """Verify server health, extension files, and the extension heartbeat."""
    from .. import chrome_install as impl

    raise typer.Exit(impl.doctor(port=port))
