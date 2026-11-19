"""Nexus CLI — delegation skill: print the self-configuring block.

``nexus skill print`` emits the same markdown served at /ext/delegate so
the user can copy the delegation skill straight from the terminal and
paste it into Claude Code / OpenCode / Codex; the agent then self-installs
it (own skill file or AGENTS.md) and verifies `nexus bots list`.
"""

from __future__ import annotations

import typer

from ..ext_skill import SKILL_NAME, delegation_skill_markdown

skill_app = typer.Typer(help="Delegation skill for external coding agents", no_args_is_help=True)


@skill_app.command("print")
def print_skill(
    port: int = typer.Option(None, "--port", help="Server port to note in the block (default: config)."),
) -> None:
    """Print the Nexus delegation skill to stdout (copy & paste into your agent)."""
    resolved = port
    if resolved is None:
        try:
            from ..config import PORT

            resolved = int(PORT)
        except Exception:
            resolved = None
    typer.echo(delegation_skill_markdown(port=resolved))
    typer.echo(f"# skill: {SKILL_NAME} — paste into your agent; it self-installs", err=True)
