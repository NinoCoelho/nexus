"""Nexus CLI — daemon subcommand group."""

from __future__ import annotations

import typer

daemon_app = typer.Typer(help="Daemon management commands", no_args_is_help=True)


@daemon_app.command("start")
def daemon_start(
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
    port: int = typer.Option(18989, "--port", "-p", help="Port to bind to"),
    detach: bool = typer.Option(True, "--detach/--no-detach", help="Run as daemon or in foreground"),
    no_frontend: bool = typer.Option(False, "--no-frontend", help="Skip launching the frontend dev server"),
) -> None:
    """Start the Nexus daemon."""
    from ..daemon import daemon_manager
    if no_frontend:
        from ..daemon import DaemonManager
        original = DaemonManager._start_frontend
        DaemonManager._start_frontend = lambda self: None
        try:
            daemon_manager.start(host=host, port=port, detach=detach)
        finally:
            DaemonManager._start_frontend = original
    else:
        daemon_manager.start(host=host, port=port, detach=detach)


@daemon_app.command("stop")
def daemon_stop() -> None:
    """Stop the Nexus daemon."""
    from ..daemon import daemon_manager
    daemon_manager.stop()


@daemon_app.command("restart")
def daemon_restart(
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
    port: int = typer.Option(18989, "--port", "-p", help="Port to bind to"),
) -> None:
    """Restart the Nexus daemon."""
    from ..daemon import daemon_manager
    daemon_manager.restart(host=host, port=port)


@daemon_app.command("status")
def daemon_status() -> None:
    """Show daemon status."""
    from ..daemon import daemon_manager
    daemon_manager.show_status()


@daemon_app.command("install")
def daemon_install(
    user: bool = typer.Option(True, "--user/--system", help="Install as user or system service"),
) -> None:
    """Install Nexus as a system service."""
    from ..daemon import service_installer
    service_installer.install_service(user=user)


@daemon_app.command("uninstall")
def daemon_uninstall(
    user: bool = typer.Option(True, "--user/--system", help="Uninstall user or system service"),
) -> None:
    """Uninstall Nexus system service."""
    from ..daemon import service_installer
    service_installer.uninstall_service(user=user)


@daemon_app.command("logs")
def daemon_logs(
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output (press 'q' to quit)"),
) -> None:
    """Show daemon logs."""
    from ..daemon import daemon_manager
    daemon_manager.show_logs(lines=lines, follow=follow)
