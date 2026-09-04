"""Nexus CLI — graphrag subcommand group."""

from __future__ import annotations

import typer

graphrag_app = typer.Typer(help="GraphRAG commands", no_args_is_help=True)


def _ensure_engine() -> None:
    """Initialize the GraphRAG engine in the calling process if not already up.

    The web server initializes the engine in its lifespan; the CLI is its
    own process, so it has to do the same before calling get_engine().
    """
    import asyncio
    from ..agent.graphrag_manager import get_engine, initialize
    from ..config_file import load as load_config

    if get_engine() is not None:
        return
    cfg = load_config()
    if not cfg.graphrag.enabled:
        typer.echo("GraphRAG is not enabled in config.", err=True)
        raise typer.Exit(1)
    asyncio.run(initialize(cfg))


@graphrag_app.command("stats")
def graphrag_stats() -> None:
    """Show entity / relation counts for the knowledge graph."""
    from ..agent.graphrag_manager import get_engine
    from rich.console import Console
    from rich.table import Table

    _ensure_engine()
    engine = get_engine()
    if engine is None:
        typer.echo("GraphRAG engine unavailable.", err=True)
        raise typer.Exit(1)
    graph = engine._entity_graph
    components = graph.connected_components()

    summary = Table(title="Knowledge Graph", show_header=False)
    summary.add_row("entities", str(graph.count_entities()))
    summary.add_row("triples", str(graph.count_triples()))
    summary.add_row("components", str(len(components)))
    Console().print(summary)

    types = graph.entity_counts_by_type()
    if types:
        by_type = Table(title="Entities by type")
        by_type.add_column("Type")
        by_type.add_column("Count", justify="right")
        for t, n in sorted(types.items(), key=lambda kv: -kv[1]):
            by_type.add_row(t, str(n))
        Console().print(by_type)


@graphrag_app.command("query")
def graphrag_query(
    text: str = typer.Argument(..., help="Natural-language query."),
    limit: int = typer.Option(5, "--limit", "-n"),
) -> None:
    """Run a semantic query against the knowledge graph."""
    import asyncio
    from ..agent.graphrag_manager import get_engine
    from rich.console import Console

    _ensure_engine()
    engine = get_engine()
    if engine is None:
        typer.echo("GraphRAG engine unavailable.", err=True)
        raise typer.Exit(1)

    enriched = asyncio.run(engine.retrieve_enriched(text, top_k=limit))
    console = Console()
    if not enriched.results:
        console.print("[dim]No matches.[/dim]")
        return
    for i, r in enumerate(enriched.results, 1):
        console.print(f"[bold]{i}. {r.heading or r.source_path}[/bold]  [dim]score={r.score:.3f}[/dim]")
        console.print(f"   [cyan]{r.source_path}[/cyan]")
        snippet = (r.content or "").strip().replace("\n", " ")
        if len(snippet) > 240:
            snippet = snippet[:237] + "…"
        console.print(f"   {snippet}")
        if r.related_entities:
            console.print(f"   [dim]entities: {', '.join(r.related_entities[:6])}[/dim]")
        console.print()


@graphrag_app.command("reindex")
def graphrag_reindex(
    incremental: bool = typer.Option(
        False, "--incremental", "-i",
        help="Skip the drop and only index files whose content changed.",
    ),
) -> None:
    """Drop GraphRAG data and rebuild the index from vault files."""
    import asyncio
    import json as _json
    from rich.console import Console

    from ..agent.graphrag_manager import index_vault_streaming
    from ..config_file import load as load_config

    console = Console()
    cfg = load_config()
    if not cfg.graphrag.enabled:
        typer.echo("GraphRAG is not enabled in config.", err=True)
        raise typer.Exit(1)

    state = {"done": 0, "total": 0, "errors": 0, "last_pct": -1}

    def _progress(data: dict) -> None:
        state["done"] = data.get("files_done", state["done"])
        state["total"] = data.get("files_total", state["total"])
        total = state["total"] or 1
        pct = int(100 * state["done"] / total)
        # Print at most one line per percent — a 3,000-file vault must not
        # look frozen, but also must not flood the terminal per file.
        if pct != state["last_pct"]:
            state["last_pct"] = pct
            console.print(
                f"[dim]\\r{state['done']}/{state['total']} files "
                f"({pct}%) · {data.get('entities', '?')} entities · "
                f"{data.get('triples', '?')} relations[/dim]",
                end="",
                soft_wrap=True,
            )

    async def _run() -> None:
        if incremental:
            # The streaming path only re-initializes the engine on a full
            # rebuild; an incremental pass in a fresh CLI process needs the
            # engine brought up first.
            from ..agent.graphrag_manager import get_engine, initialize
            if get_engine() is None:
                await initialize(cfg)
        async for frame in index_vault_streaming(cfg, full=not incremental):
            lines = frame.strip().split("\n")
            event = ""
            payload: dict = {}
            for ln in lines:
                if ln.startswith("event:"):
                    event = ln[6:].strip()
                elif ln.startswith("data:"):
                    try:
                        payload = _json.loads(ln[5:].strip())
                    except _json.JSONDecodeError:
                        payload = {}
            if event == "status":
                console.print(f"[dim]{payload.get('message', '')}[/dim]")
            elif event == "file":
                _progress(payload)
            elif event == "error":
                state["errors"] += 1
                path = payload.get("path") or ""
                console.print(f"[red]error:[/red] {path} {payload.get('detail', '')}")
            elif event == "stats":
                console.print()  # finish the progress line
                s = payload
                console.print(
                    f"[green]Indexed[/green] {s.get('files_indexed', 0)} file(s) "
                    f"({s.get('files_skipped', 0)} skipped) in {s.get('elapsed_s', '?')}s — "
                    f"{s.get('entities', 0)} entities, {s.get('triples', 0)} relations "
                    f"(+{s.get('entities_added', 0)} entities, +{s.get('triples_added', 0)} relations)"
                )
                if state["errors"]:
                    console.print(f"[yellow]{state['errors']} file(s) failed — see log above.[/yellow]")

    asyncio.run(_run())
    typer.echo("GraphRAG reindex complete.")
