"""Nexus CLI — kanban subcommand group.

Thin wrappers over the ``kanban_manage`` agent-tool handler, so external
agents get exactly the same board operations the in-Nexus agent has.
Boards are vault markdown (``kanban-plugin: basic``) — no daemon needed;
the CLI talks to the vault files directly.
"""

from __future__ import annotations

import json

import typer

kanban_app = typer.Typer(help="Kanban boards stored in the vault", no_args_is_help=True)


def _invoke(args: dict) -> dict:
    from ..tools.kanban_tool import handle_kanban_tool

    raw = handle_kanban_tool(args)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "error": raw}


def _resolve_card(path: str, ref: str) -> str:
    """Accept a card id OR a title (exact, then substring); return the id."""
    from ..vault_kanban.boards import read_board

    board = read_board(path)
    for lane in board.lanes:
        for card in lane.cards:
            if card.id == ref:
                return card.id
    for lane in board.lanes:
        for card in lane.cards:
            if card.title == ref:
                return card.id
    for lane in board.lanes:
        for card in lane.cards:
            if ref.lower() in card.title.lower():
                return card.id
    return ref  # let the handler report the error


def _fail_if_error(res: dict) -> None:
    if not res.get("ok", True):
        typer.echo(f"error: {res.get('error', res)}", err=True)
        raise typer.Exit(1)


@kanban_app.command("boards")
def boards(json_out: bool = typer.Option(False, "--json")) -> None:
    """List every kanban board in the vault."""
    from ..vault_kanban.boards import list_boards as _list_boards

    items = _list_boards()
    if json_out:
        typer.echo(json.dumps(items, ensure_ascii=False, indent=2))
        return
    if not items:
        typer.echo("No boards.")
        return
    for b in items:
        title = b.get("title", "?")
        path = b.get("path", "?")
        typer.echo(f"{path:<40} {title}")


@kanban_app.command("show")
def show(
    path: str = typer.Argument(..., help="Board vault path (e.g. boards/work.md)."),
    full: bool = typer.Option(False, "--full", help="Include every card body verbatim."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """View a board (lanes + cards)."""
    res = _invoke({"action": "view", "path": path, "full": full})
    _fail_if_error(res)
    if json_out:
        typer.echo(json.dumps(res, ensure_ascii=False, indent=2))
        return
    board = res.get("board") or res
    typer.echo(f"# {board.get('title', path)}")
    for lane in board.get("lanes", []):
        typer.echo(f"\n## {lane.get('title', '?')} ({len(lane.get('cards', []))})")
        for card in lane.get("cards", []):
            body = card.get("body", "")
            preview = body[:100].replace("\n", " ") if body else ""
            suffix = f" — {preview}…" if len(body) > 100 else (f" — {preview}" if preview else "")
            typer.echo(f"  [{card.get('id', '?')}] {card.get('title', '?')}{suffix}")


@kanban_app.command("create")
def create(
    path: str = typer.Argument(..., help="New board vault path (e.g. boards/sprint-42.md)."),
    title: str | None = typer.Option(None, "--title"),
    lanes: str | None = typer.Option(None, "--lanes", help="Comma-separated lane titles (default: Todo,Doing,Done)."),
) -> None:
    """Create a new board."""
    args: dict = {"action": "create_board", "path": path}
    if title:
        args["title"] = title
    if lanes:
        args["columns"] = [c.strip() for c in lanes.split(",") if c.strip()]
    res = _invoke(args)
    _fail_if_error(res)
    typer.echo(f"Created board {path}.")


@kanban_app.command("add")
def add(
    path: str = typer.Argument(..., help="Board vault path."),
    lane: str = typer.Argument(..., help="Lane title (or id) to add the card to."),
    title: str = typer.Argument(..., help="Card title."),
    body: str | None = typer.Option(None, "--body", help="Card body text."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Add a card to a lane."""
    args: dict = {"action": "add_card", "path": path, "lane": lane, "title": title}
    if body:
        args["body"] = body
    res = _invoke(args)
    _fail_if_error(res)
    if json_out:
        typer.echo(json.dumps(res, ensure_ascii=False, indent=2))
        return
    card = res.get("card") or {}
    typer.echo(f"Added card {card.get('id', '')} to {lane}.")


@kanban_app.command("move")
def move(
    path: str = typer.Argument(..., help="Board vault path."),
    card: str = typer.Argument(..., help="Card id or title (substring ok)."),
    lane: str = typer.Argument(..., help="Destination lane id or title."),
) -> None:
    """Move a card to another lane."""
    res = _invoke({
        "action": "move_card", "path": path,
        "card_id": _resolve_card(path, card), "lane": lane,
    })
    _fail_if_error(res)
    typer.echo(f"Moved {card} → {lane}.")


@kanban_app.command("update")
def update(
    path: str = typer.Argument(..., help="Board vault path."),
    card: str = typer.Argument(..., help="Card id or title (substring ok)."),
    title: str | None = typer.Option(None, "--title"),
    body: str | None = typer.Option(None, "--body"),
) -> None:
    """Update a card's title and/or body."""
    args: dict = {"action": "update_card", "path": path, "card_id": _resolve_card(path, card)}
    if title:
        args["title"] = title
    if body:
        args["body"] = body
    res = _invoke(args)
    _fail_if_error(res)
    typer.echo(f"Updated {card}.")


@kanban_app.command("rm")
def rm(
    path: str = typer.Argument(..., help="Board vault path."),
    card: str = typer.Argument(..., help="Card id or title (substring ok)."),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete a card."""
    if not yes:
        typer.confirm(f"Delete card '{card}' from {path}?", abort=True)
    res = _invoke({"action": "delete_card", "path": path, "card_id": _resolve_card(path, card)})
    _fail_if_error(res)
    typer.echo(f"Deleted {card}.")


@kanban_app.command("lane")
def lane(
    path: str = typer.Argument(..., help="Board vault path."),
    title: str = typer.Argument(..., help="New lane title."),
) -> None:
    """Add a lane to a board."""
    res = _invoke({"action": "add_lane", "path": path, "title": title})
    _fail_if_error(res)
    typer.echo(f"Added lane {title}.")
