"""Nexus CLI — interactive chat command."""

from __future__ import annotations

from typing import Any, Optional

import typer


def register(app: typer.Typer) -> None:
    """Register the chat command onto the given Typer app."""
    app.command()(chat)


def chat(
    session: Optional[str] = typer.Option(None, "--session"),
    model: Optional[str] = typer.Option(None, "--model"),
    context: Optional[str] = typer.Option(None, "--context"),
    port: int = typer.Option(18989, "--port"),
) -> None:
    """Interactive chat loop (requires nexus serve running)."""
    import httpx
    base = f"http://127.0.0.1:{port}"

    # Check server health
    try:
        r = httpx.get(f"{base}/health", timeout=3)
        r.raise_for_status()
    except Exception:
        typer.echo(
            f"Cannot reach Nexus server at {base}. Run `nexus serve` in another terminal.",
            err=True,
        )
        raise typer.Exit(1)

    try:
        from rich.console import Console
        from rich.markdown import Markdown
        console = Console()
        use_rich = True
    except ImportError:
        use_rich = False

    try:
        from prompt_toolkit import PromptSession
        pt_session: Any = PromptSession()
        use_pt = True
    except ImportError:
        use_pt = False

    sid = session
    typer.echo("Nexus chat — type 'exit' or Ctrl-D to quit.\n")

    while True:
        try:
            if use_pt:
                line = pt_session.prompt("you> ")
            else:
                line = input("you> ")
        except (EOFError, KeyboardInterrupt):
            typer.echo("")
            break

        line = line.strip()
        if not line:
            continue
        if line.lower() in {"exit", "quit"}:
            break

        payload: dict = {"message": line}
        if sid:
            payload["session_id"] = sid
        if context:
            payload["context"] = context
        if model:
            payload["model"] = model

        try:
            resp = httpx.post(f"{base}/chat", json=payload, timeout=120)
            resp.raise_for_status()
        except Exception as exc:
            typer.echo(f"[error] {exc}", err=True)
            continue

        data = resp.json()
        sid = data.get("session_id", sid)

        # Print tool traces in gray
        for event in data.get("trace", []):
            name = event.get("name") or event.get("event", "")
            if name and name not in {"_meta", "iter", "reply", "tool_result"}:
                args = event.get("args", event.get("data", {}))
                if use_rich:
                    console.print(f"[dim]  tool: {name} {args}[/dim]")
                else:
                    typer.echo(f"  tool: {name} {args}")

        reply = data.get("reply", "")
        if use_rich:
            console.print(Markdown(reply))
        else:
            typer.echo(f"\n{reply}\n")
