"""Card CRUD operations on vault kanban boards."""

from __future__ import annotations

import uuid
from typing import Any

from .boards import read_board, write_board
from .models import CARD_PRIORITIES, CARD_STATUSES, Board, Card, Lane


def _find_card(board: Board, card_id: str) -> tuple[Lane, Card, int] | None:
    for lane in board.lanes:
        for idx, card in enumerate(lane.cards):
            if card.id == card_id:
                return lane, card, idx
    return None


def _find_lane(board: Board, lane_id: str) -> Lane | None:
    for lane in board.lanes:
        if lane.id == lane_id:
            return lane
    return None


def _coerce_str_list(raw: Any) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if str(s).strip()]
    if isinstance(raw, str):
        return [s.strip() for s in raw.split(",") if s.strip()]
    return []


def add_card(
    path: str,
    lane_id: str,
    title: str,
    body: str = "",
) -> Card:
    import nexus.vault_kanban.hooks as _hooks_mod

    board = read_board(path)
    lane = _find_lane(board, lane_id)
    if lane is None:
        raise KeyError(f"lane {lane_id!r} not found")
    card = Card(id=str(uuid.uuid4()), title=title, body=body)
    lane.cards.append(card)
    write_board(path, board)

    # Fire the lane-change hook for "card lands in this lane" symmetry: a
    # new card created directly in a prompt-bearing lane should run the
    # prompt, just like a move into that lane does. ``src_lane_id`` is empty
    # to flag a fresh card with no source lane.
    hook = _hooks_mod._lane_change_hook
    if hook is not None:
        try:
            hook(
                path=path,
                card_id=card.id,
                src_lane_id="",
                dst_lane_id=lane.id,
                dst_lane_prompt=lane.prompt,
            )
        except Exception:
            import logging
            logging.getLogger(__name__).exception("lane_change_hook raised on add_card")
    return card


def update_card(
    path: str,
    card_id: str,
    updates: dict[str, Any],
) -> Card:
    board = read_board(path)
    found = _find_card(board, card_id)
    if found is None:
        raise KeyError(f"card {card_id!r} not found")
    _, card, _ = found
    if "title" in updates:
        card.title = str(updates["title"])
    if "body" in updates:
        card.body = str(updates["body"])
    if "session_id" in updates:
        sid = updates["session_id"]
        card.session_id = str(sid) if sid else None
    if "status" in updates:
        raw = updates["status"]
        if raw is None or raw == "":
            card.status = None
        elif raw in CARD_STATUSES:
            card.status = raw
        else:
            raise ValueError(f"invalid status {raw!r}; allowed: {sorted(CARD_STATUSES)}")
    if "due" in updates:
        raw = updates["due"]
        card.due = str(raw).strip() if raw else None
    if "priority" in updates:
        raw = updates["priority"]
        if raw is None or raw == "":
            card.priority = None
        elif raw in CARD_PRIORITIES:
            card.priority = raw
        else:
            raise ValueError(f"invalid priority {raw!r}; allowed: {sorted(CARD_PRIORITIES)}")
    if "labels" in updates:
        raw = updates["labels"]
        card.labels = _coerce_str_list(raw)
    if "assignees" in updates:
        raw = updates["assignees"]
        card.assignees = _coerce_str_list(raw)
    write_board(path, board)
    return card


def move_card(
    path: str,
    card_id: str,
    lane_id: str,
    position: int | None = None,
) -> Card:
    import nexus.vault_kanban.hooks as _hooks_mod

    board = read_board(path)
    found = _find_card(board, card_id)
    if found is None:
        raise KeyError(f"card {card_id!r} not found")
    src_lane, card, src_idx = found
    dst_lane = _find_lane(board, lane_id)
    if dst_lane is None:
        raise KeyError(f"lane {lane_id!r} not found")
    src_lane.cards.pop(src_idx)
    if position is None or position >= len(dst_lane.cards):
        dst_lane.cards.append(card)
    else:
        dst_lane.cards.insert(max(0, position), card)
    write_board(path, board)

    # Fire the lane-change hook *after* persisting. Cross-lane only — staying
    # within the same lane is just a reorder, never an auto-dispatch trigger.
    hook = _hooks_mod._lane_change_hook
    if hook is not None and src_lane.id != dst_lane.id:
        try:
            hook(
                path=path,
                card_id=card.id,
                src_lane_id=src_lane.id,
                dst_lane_id=dst_lane.id,
                dst_lane_prompt=dst_lane.prompt,
            )
        except Exception:
            # Never let a misbehaving hook break a successful move.
            import logging
            logging.getLogger(__name__).exception("lane_change_hook raised")
    return card


def delete_card(path: str, card_id: str) -> None:
    board = read_board(path)
    found = _find_card(board, card_id)
    if found is None:
        raise KeyError(f"card {card_id!r} not found")
    lane, _, idx = found
    lane.cards.pop(idx)
    write_board(path, board)
