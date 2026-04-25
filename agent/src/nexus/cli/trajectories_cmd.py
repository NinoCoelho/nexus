"""Nexus CLI — trajectories subcommand group."""

from __future__ import annotations

from typing import Optional

import typer

trajectories_app = typer.Typer(help="Trajectory commands", no_args_is_help=True)


@trajectories_app.command("list")
def trajectories_list(
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """List sessions with trajectory records, newest first."""
    import json
    from collections import Counter
    from datetime import datetime
    from pathlib import Path
    from rich.console import Console
    from rich.table import Table

    base = Path("~/.nexus/trajectories").expanduser()
    if not base.is_dir():
        typer.echo("No trajectories — set NEXUS_TRAJECTORIES=1 to start logging.")
        return
    counts: Counter[str] = Counter()
    last_seen: dict[str, int] = {}
    titles: dict[str, str] = {}
    for f in sorted(base.glob("trajectories-*.jsonl")):
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = rec.get("session_id") or ""
                if not sid:
                    continue
                counts[sid] += 1
                ts = int(rec.get("timestamp") or 0)
                if ts > last_seen.get(sid, 0):
                    last_seen[sid] = ts
                if sid not in titles:
                    state = rec.get("state") or {}
                    if isinstance(state, dict):
                        titles[sid] = str(state.get("user_message", ""))[:60]
        except OSError:
            continue
    if not counts:
        typer.echo("No trajectory records found.")
        return
    rows = sorted(counts.items(), key=lambda kv: -last_seen.get(kv[0], 0))[:limit]
    table = Table(title=f"Trajectories ({sum(counts.values())} total in {len(counts)} sessions)")
    table.add_column("Session", style="dim")
    table.add_column("Records", justify="right")
    table.add_column("Last seen")
    table.add_column("First message")
    for sid, n in rows:
        when = datetime.fromtimestamp(last_seen.get(sid, 0)).strftime("%Y-%m-%d %H:%M") if last_seen.get(sid) else ""
        table.add_row(sid[:12], str(n), when, titles.get(sid, ""))
    Console().print(table)


@trajectories_app.command("show")
def trajectories_show(
    session_id: str = typer.Argument(...),
    limit: int = typer.Option(20, "--limit", "-n"),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a summary."),
) -> None:
    """Print trajectory records for a single session, newest first."""
    import json as _json
    from datetime import datetime
    from rich.console import Console

    from ..trajectory import TrajectoryLogger

    logger = TrajectoryLogger()
    records = logger.find_for_session(session_id, limit=max(1, min(limit, 200)))
    if not records:
        typer.echo("No records for that session.")
        return
    if json_out:
        for r in records:
            typer.echo(_json.dumps(r, ensure_ascii=False))
        return
    console = Console()
    for r in records:
        ts = datetime.fromtimestamp(int(r.get("timestamp") or 0)).strftime("%Y-%m-%d %H:%M:%S")
        console.print(f"[bold]turn {r.get('turn_index')}[/bold]  {ts}  [dim]{r.get('trajectory_id', '')[:8]}[/dim]")
        action = r.get("action") or {}
        if isinstance(action, dict):
            tools = action.get("tool_calls") or []
            if isinstance(tools, list) and tools:
                names = ", ".join(str(t.get("name", "")) for t in tools if isinstance(t, dict))
                console.print(f"  tools: {names}")
        reward = r.get("reward") or {}
        if isinstance(reward, dict) and reward:
            console.print(f"  reward: {reward}")
        console.print()


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
