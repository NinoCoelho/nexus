"""High-level board I/O and cross-board query."""

from __future__ import annotations

from typing import Any

from .. import vault
from .models import Board, Lane, is_kanban_file
from .parser import _slug, parse, serialize


def read_board(path: str) -> Board:
    file = vault.read_file(path)
    return parse(file["content"])


def write_board(path: str, board: Board) -> None:
    vault.write_file(path, serialize(board))


def create_empty(path: str, title: str | None = None, columns: list[str] | None = None) -> Board:
    """Scaffold a new kanban file at path with default lanes."""
    cols = columns or ["Todo", "Doing", "Done"]
    board = Board(
        title=title or path.rsplit("/", 1)[-1].removesuffix(".md").replace("-", " ").title() or "Kanban",
        frontmatter={"kanban-plugin": "basic"},
        lanes=[Lane(id=_slug(c), title=c) for c in cols],
    )
    write_board(path, board)
    return board


def query_boards(
    *,
    text: str | None = None,
    label: str | None = None,
    assignee: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    due_before: str | None = None,
    due_after: str | None = None,
    lane: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Search every kanban board in the vault for cards matching the criteria.

    Returns a flat list of hit dicts: ``{path, board_title, lane_id, lane_title,
    card_id, title, body, due, priority, labels, assignees, status, session_id}``.
    All filters are AND-combined; a missing filter matches everything. ``text``
    is case-insensitive substring match against title + body + labels.
    """
    text_q = text.lower().strip() if text else None
    hits: list[dict[str, Any]] = []
    for entry in vault.list_tree():
        if entry.type != "file":
            continue
        path = entry.path
        if not path.endswith(".md"):
            continue
        try:
            file = vault.read_file(path)
        except (FileNotFoundError, OSError):
            continue
        if not is_kanban_file(file["content"]):
            continue
        try:
            board = parse(file["content"])
        except Exception:
            continue
        for ln in board.lanes:
            if lane and ln.id != lane and ln.title != lane:
                continue
            for card in ln.cards:
                if status and card.status != status:
                    continue
                if priority and card.priority != priority:
                    continue
                if label and label not in card.labels:
                    continue
                if assignee and assignee not in card.assignees:
                    continue
                if due_before and (not card.due or card.due > due_before):
                    continue
                if due_after and (not card.due or card.due < due_after):
                    continue
                if text_q:
                    haystack = " ".join([
                        card.title or "",
                        card.body or "",
                        ",".join(card.labels),
                        ",".join(card.assignees),
                    ]).lower()
                    if text_q not in haystack:
                        continue
                hits.append({
                    "path": path,
                    "board_title": board.title,
                    "lane_id": ln.id,
                    "lane_title": ln.title,
                    "card_id": card.id,
                    "title": card.title,
                    "body": card.body,
                    "due": card.due,
                    "priority": card.priority,
                    "labels": list(card.labels),
                    "assignees": list(card.assignees),
                    "status": card.status,
                    "session_id": card.session_id,
                })
                if len(hits) >= limit:
                    return hits
    return hits
