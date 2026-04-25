"""Nexus CLI — entry point: nexus."""

from __future__ import annotations

import typer

from .backup_cmd import backup_app
from .config_cmd import config_app
from .daemon_cmd import daemon_app
from .graphrag_cmd import graphrag_app
from .models_cmd import models_app
from .providers_cmd import providers_app
from .sessions_cmd import sessions_app
from .skills_cmd import skills_app
from .trajectories_cmd import trajectories_app
from .vault_cmd import vault_app

app = typer.Typer(help="Nexus agent CLI", no_args_is_help=True)

app.add_typer(config_app, name="config")
app.add_typer(providers_app, name="providers")
app.add_typer(models_app, name="models")
app.add_typer(skills_app, name="skills")
app.add_typer(sessions_app, name="sessions")
app.add_typer(vault_app, name="vault")
app.add_typer(daemon_app, name="daemon")
app.add_typer(trajectories_app, name="trajectories")
app.add_typer(graphrag_app, name="graphrag")
app.add_typer(backup_app, name="backup")


@app.callback()
def _global_setup() -> None:
    """Runs before every CLI subcommand.

    Installs the redacting log formatter so subcommands that print provider
    errors, config dumps, etc. mask secrets automatically. Idempotent and
    respects NEXUS_REDACT_SECRETS=false.
    """
    from ..redact import install_redaction
    install_redaction(extra_loggers=("httpx",))


# ── serve ──────────────────────────────────────────────────────────────────────

from .serve import serve as _serve_fn  # noqa: E402

app.command()(_serve_fn)


# ── insights ───────────────────────────────────────────────────────────────────

from .insights_cmd import insights as _insights_fn  # noqa: E402

app.command()(_insights_fn)


# ── chat ───────────────────────────────────────────────────────────────────────

from .chat import chat as _chat_fn  # noqa: E402

app.command()(_chat_fn)


# ── version + doctor ──────────────────────────────────────────────────────────

from .doctor_cmd import doctor as _doctor_fn, version as _version_fn  # noqa: E402

app.command()(_version_fn)
app.command()(_doctor_fn)


if __name__ == "__main__":
    app()
