"""Routes for vault kanban board operations: /vault/kanban*."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

router = APIRouter()

# Kanban lives inside the vault as a plain .md file with
# `kanban-plugin: basic` frontmatter (Obsidian-compatible).


@router.get("/vault/kanban")
async def vault_kanban_get(path: str) -> dict:
    from ... import vault_kanban
    try:
        board = vault_kanban.read_board(path)
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file not found")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return {"path": path, **board.to_dict()}


@router.post("/vault/kanban", status_code=status.HTTP_201_CREATED)
async def vault_kanban_create(body: dict) -> dict:
    """Scaffold a new kanban .md file. Body: {path, title?, columns?}."""
    from ... import vault_kanban
    path = body.get("path", "")
    if not path:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`path` required")
    try:
        board = vault_kanban.create_empty(
            path,
            title=body.get("title"),
            columns=body.get("columns"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return {"path": path, **board.to_dict()}


@router.patch("/vault/kanban/cards/{card_id}")
async def vault_kanban_patch_card(card_id: str, body: dict, path: str) -> dict:
    """Update title/body or move between lanes. Body: {title?, body?, lane?, position?}."""
    from ... import vault_kanban
    try:
        if "lane" in body:
            card = vault_kanban.move_card(
                path, card_id, body["lane"], body.get("position"),
            )
            # Also apply any content edits in the same call.
            updates = {k: body[k] for k in ("title", "body", "session_id", "status", "due", "priority", "labels", "assignees") if k in body}
            if updates:
                card = vault_kanban.update_card(path, card_id, updates)
        else:
            updates = {k: body[k] for k in ("title", "body", "session_id", "status", "due", "priority", "labels", "assignees") if k in body}
            card = vault_kanban.update_card(path, card_id, updates)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return card.to_dict()


@router.post("/vault/kanban/cards", status_code=status.HTTP_201_CREATED)
async def vault_kanban_add_card(body: dict, path: str) -> dict:
    from ... import vault_kanban
    lane = body.get("lane", "")
    title = body.get("title", "")
    if not lane or not title:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`lane` and `title` required")
    try:
        card = vault_kanban.add_card(path, lane, title, body.get("body", ""))
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return card.to_dict()


@router.delete("/vault/kanban/cards/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
async def vault_kanban_delete_card(card_id: str, path: str) -> None:
    from ... import vault_kanban
    try:
        vault_kanban.delete_card(path, card_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("/vault/kanban/boards")
async def vault_kanban_boards() -> dict:
    """List every kanban board in the vault as ``[{path, title}]``."""
    from ... import vault_kanban
    boards = vault_kanban.list_boards()
    return {"boards": boards, "count": len(boards)}


@router.post("/vault/kanban/query")
async def vault_kanban_query(body: dict) -> dict:
    """Cross-board card search. Body keys (all optional):
    text, label, assignee, priority, status, due_before, due_after, lane, limit.
    """
    from ... import vault_kanban
    kwargs = {
        k: body[k]
        for k in (
            "text", "label", "assignee", "priority", "status",
            "due_before", "due_after", "lane",
        )
        if k in body and body[k] not in (None, "")
    }
    limit = int(body.get("limit") or 100)
    hits = vault_kanban.query_boards(limit=limit, **kwargs)
    return {"hits": hits, "count": len(hits)}


@router.post("/vault/kanban/lanes", status_code=status.HTTP_201_CREATED)
async def vault_kanban_add_lane(body: dict, path: str) -> dict:
    from ... import vault_kanban
    title = body.get("title", "")
    if not title:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`title` required")
    lane = vault_kanban.add_lane(path, title)
    return lane.to_dict()


@router.patch("/vault/kanban/lanes/{lane_id}")
async def vault_kanban_patch_lane(lane_id: str, body: dict, path: str) -> dict:
    """Update a lane's title, prompt, or auto-dispatch model. Body: {title?, prompt?, model?}."""
    from ... import vault_kanban
    try:
        lane = vault_kanban.update_lane(path, lane_id, body)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return lane.to_dict()


@router.delete("/vault/kanban/lanes/{lane_id}", status_code=status.HTTP_204_NO_CONTENT)
async def vault_kanban_delete_lane(lane_id: str, path: str) -> None:
    from ... import vault_kanban
    vault_kanban.delete_lane(path, lane_id)
