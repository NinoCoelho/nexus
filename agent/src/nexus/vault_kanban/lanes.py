"""Lane CRUD operations on vault kanban boards."""

from __future__ import annotations

from typing import Any

from .boards import read_board, write_board
from .models import Board, Lane
from .parser import _slug


def _find_lane(board: Board, lane_id: str) -> Lane | None:
    for lane in board.lanes:
        if lane.id == lane_id:
            return lane
    return None


def add_lane(path: str, title: str) -> Lane:
    board = read_board(path)
    lane = Lane(id=_slug(title), title=title)
    # Ensure lane id is unique
    existing = {l.id for l in board.lanes}
    if lane.id in existing:
        base = lane.id
        i = 2
        while f"{base}-{i}" in existing:
            i += 1
        lane.id = f"{base}-{i}"
    board.lanes.append(lane)
    write_board(path, board)
    return lane


def delete_lane(path: str, lane_id: str) -> None:
    board = read_board(path)
    board.lanes = [l for l in board.lanes if l.id != lane_id]
    write_board(path, board)


def update_lane(path: str, lane_id: str, updates: dict[str, Any]) -> Lane:
    board = read_board(path)
    lane = _find_lane(board, lane_id)
    if lane is None:
        raise KeyError(f"lane {lane_id!r} not found")
    if "title" in updates:
        lane.title = str(updates["title"])
    if "prompt" in updates:
        raw = updates["prompt"]
        lane.prompt = str(raw).strip() if raw else None
    if "model" in updates:
        raw = updates["model"]
        lane.model = str(raw).strip() if raw else None
    write_board(path, board)
    return lane
