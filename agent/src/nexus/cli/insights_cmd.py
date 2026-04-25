"""Nexus CLI — insights command."""

from __future__ import annotations

import typer


def register(app: typer.Typer) -> None:
    """Register the insights command onto the given Typer app."""
    app.command()(insights)


def insights(
    days: int = typer.Option(30, "--days", "-d", help="Look-back window in days."),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of the terminal report."),
) -> None:
    """Analyze your session history — sessions, tools, activity patterns."""
    from ..insights import InsightsEngine, format_terminal
    from ..server.session_store import _DB_PATH

    engine = InsightsEngine(_DB_PATH)
    report = engine.generate(days=max(1, min(int(days), 365)))

    if json_out:
        import json as _json
        typer.echo(_json.dumps(report, indent=2, default=str))
        return

    typer.echo(format_terminal(report))
