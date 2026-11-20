"""Nexus CLI — ``nexus browser``: managed-browser status and NBIE metrics.

Talks to the running server over loopback (same pattern as tunnel_cmd /
pair_cmd): ``GET /browser/nbie-report`` aggregates the in-process NBIE
telemetry ring buffer — per-level inspection counts and the read
cache-hit rate — so you can see whether agents actually work text-first.
"""

from __future__ import annotations

import typer

from .tunnel_cmd import _request

browser_app = typer.Typer(help="Managed-browser status and NBIE perception metrics.")


def _fmt_rate(rate: float | None) -> str:
    return f"{rate * 100:.1f}%" if rate is not None else "—"


def _print_report(p: dict) -> None:
    totals = p.get("totals") or {}
    if not totals:
        typer.echo("No browser activity recorded yet (the buffer is in-process — "
                   "it fills as the browser tool is used against this daemon).")
        return
    typer.echo("NBIE perception report")
    typer.echo("")
    typer.echo(
        f"  actions {totals.get('actions', 0)}, "
        f"errors {totals.get('errors', 0)}, "
        f"reads {totals.get('reads', 0)}, "
        f"cache hits {totals.get('cache_hits', 0)} "
        f"({_fmt_rate(totals.get('cache_hit_rate'))})"
    )
    levels = p.get("levels") or {}
    if levels:
        typer.echo("  levels:   " + ", ".join(f"{k} {v}" for k, v in sorted(levels.items())))
    surfaces = p.get("by_surface") or {}
    if surfaces:
        parts = []
        for s, d in sorted(surfaces.items()):
            rate = d["cache_hits"] / d["reads"] if d.get("reads") else None
            parts.append(f"{s} {d['actions']}a/{d['reads']}r/{_fmt_rate(rate)}")
        typer.echo("  surfaces: " + ", ".join(parts))
    actions = p.get("actions") or {}
    if actions:
        typer.echo("  actions:  " + ", ".join(f"{k} {v}" for k, v in sorted(actions.items())))
    keys = p.get("keys") or {}
    if keys:
        typer.echo("  contexts: " + ", ".join(f"{k} {v}" for k, v in sorted(keys.items())))
    errors = p.get("recent_errors") or []
    if errors:
        typer.echo("  recent errors:")
        for e in errors:
            typer.echo(f"    [{e.get('key')}] {e.get('action')}: {e.get('error')}")


@browser_app.command("report")
def report(
    minutes: float = typer.Option(
        0, "--minutes", "-m", help="Only count the trailing N minutes (0 = everything buffered)."
    ),
) -> None:
    """Show NBIE perception metrics: inspection levels + read cache-hit rate.

    Requires the Nexus server to be running (`nexus daemon start`). The
    buffer is in-process — it reflects activity since the daemon booted.
    """
    path = "/browser/nbie-report" + (f"?minutes={minutes}" if minutes > 0 else "")
    _print_report(_request("GET", path))
