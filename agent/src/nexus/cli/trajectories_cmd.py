"""Nexus CLI — trajectories subcommand group."""

from __future__ import annotations

from typing import Optional

import typer

trajectories_app = typer.Typer(help="Trajectory commands", no_args_is_help=True)


@trajectories_app.command("export")
def trajectories_export(
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="Output file path (default: trajectories-export.jsonl in cwd)"
    ),
    since: Optional[str] = typer.Option(
        None, "--since", help="Include only records from this date onwards (YYYY-MM-DD)"
    ),
) -> None:
    """Export trajectory records to a JSONL file."""
    from pathlib import Path
    from ..trajectory import TrajectoryLogger

    out_path = Path(output) if output else Path("trajectories-export.jsonl")
    logger = TrajectoryLogger()
    try:
        count = logger.export(out_path, since_date=since)
    except Exception as exc:
        typer.echo(f"[error] {exc}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Exported {count} records to {out_path}")
