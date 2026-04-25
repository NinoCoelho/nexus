"""Nexus CLI — graphrag subcommand group."""

from __future__ import annotations

import typer

graphrag_app = typer.Typer(help="GraphRAG commands", no_args_is_help=True)


@graphrag_app.command("reindex")
def graphrag_reindex() -> None:
    """Drop GraphRAG data and rebuild the index from vault files."""
    import asyncio
    from ..agent.graphrag_manager import drop_data, initialize, index_full_vault
    from ..config_file import load as load_config

    cfg = load_config()
    if not cfg.graphrag.enabled:
        typer.echo("GraphRAG is not enabled in config.", err=True)
        raise typer.Exit(1)

    dropped = drop_data()
    typer.echo(f"Dropped {dropped} GraphRAG database file(s).")

    async def _run() -> None:
        await initialize(cfg)
        typer.echo("Engine initialized. Indexing vault…")
        await index_full_vault()

    asyncio.run(_run())
    typer.echo("GraphRAG reindex complete.")
