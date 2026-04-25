"""Nexus CLI — serve command."""

from __future__ import annotations

import typer

app = typer.Typer(help="Nexus agent CLI", no_args_is_help=True)


@app.command()
def serve(
    port: int = typer.Option(18989, "--port", "-p"),
    host: str = typer.Option("127.0.0.1", "--host"),
    frontend_port: int = typer.Option(1890, "--frontend-port", "-fp"),
    no_frontend: bool = typer.Option(False, "--no-frontend", help="Skip launching the frontend dev server"),
    bundled: bool = typer.Option(False, "--bundled", help="Force serving the built UI from the backend on a single port. Run `npm run build` first."),
    dev: bool = typer.Option(False, "--dev", help="Force spawning the Vite dev server on --frontend-port (two-port dev mode)."),
) -> None:
    """Start the Nexus server.

    Default: single-port mode — if ``ui/dist`` exists, the backend serves the
    built UI on ``--port``. Otherwise spawns the Vite dev server on
    ``--frontend-port``. Use ``--dev`` to force Vite, ``--bundled`` to require
    a built UI.
    """
    import shutil
    import signal
    import subprocess
    import threading
    import uvicorn

    from ..config import get_frontend_dir

    if bundled and dev:
        typer.echo("--bundled and --dev are mutually exclusive.", err=True)
        raise typer.Exit(code=2)

    fe = get_frontend_dir()
    dist = (fe / "dist") if fe is not None else None
    has_dist = dist is not None and (dist / "index.html").is_file()

    # Auto-pick: prefer bundled (single port) when a built UI is available,
    # fall back to spawning Vite for active development.
    if not bundled and not dev and not no_frontend:
        bundled = has_dist

    if bundled:
        no_frontend = True
        if not has_dist:
            typer.echo(
                "No built UI found. Run `npm run build` in ui/ first, "
                "or set NEXUS_UI_DIST to a dist directory.",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(f"Serving bundled UI from {dist} at http://{host}:{port}")

    frontend_dir = None if no_frontend else get_frontend_dir()
    frontend_proc: list[subprocess.Popen] = []
    shutting_down = False

    if not no_frontend and frontend_dir is None:
        # Explicit diagnostic — the previous silent fallback made users
        # think the UI was broken when the installed tool just can't see
        # the repo's ui/ folder. Point them at the env var override.
        typer.echo(
            "Frontend dir not found — UI not started.\n"
            "Set NEXUS_FRONTEND_DIR to your checkout's ui/ path, e.g.:\n"
            "  NEXUS_FRONTEND_DIR=~/Code/nexus/ui nexus serve\n"
            "Or pass --no-frontend to suppress this warning.",
            err=True,
        )

    if frontend_dir is not None:
        npm = shutil.which("npm")
        if npm is None:
            typer.echo("Warning: npm not found — skipping frontend.", err=True)
        else:
            typer.echo(f"Installing frontend deps in {frontend_dir} …")
            subprocess.run([npm, "install"], cwd=str(frontend_dir), check=True, capture_output=True)
            typer.echo(f"Starting frontend dev server on http://localhost:{frontend_port}")
            fp = subprocess.Popen(
                [npm, "run", "dev", "--", "--port", str(frontend_port)],
                cwd=str(frontend_dir),
            )
            frontend_proc.append(fp)

    backend_done = threading.Event()

    def run_backend() -> None:
        uvicorn.run("nexus.main:app", host=host, port=port, reload=False)
        backend_done.set()

    backend_thread = threading.Thread(target=run_backend, daemon=True)
    backend_thread.start()

    def _shutdown(*_args: object) -> None:
        nonlocal shutting_down
        if shutting_down:
            return
        shutting_down = True
        if frontend_proc:
            frontend_proc[0].terminate()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        backend_thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        if frontend_proc:
            frontend_proc[0].terminate()
            try:
                frontend_proc[0].wait(timeout=5)
            except subprocess.TimeoutExpired:
                frontend_proc[0].kill()
        typer.echo("Nexus stopped.")
