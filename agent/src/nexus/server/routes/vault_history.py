"""Routes for vault history (opt-in, git-backed): /vault/history*."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

router = APIRouter()


@router.get("/vault/history/status")
async def vault_history_status() -> dict:
    from ... import vault_history
    return vault_history.status()


@router.post("/vault/history/enable")
async def vault_history_enable() -> dict:
    from ... import vault_history
    try:
        return vault_history.enable()
    except vault_history.HistoryError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.post("/vault/history/disable")
async def vault_history_disable() -> dict:
    from ... import vault_history
    return vault_history.disable()


@router.get("/vault/history")
async def vault_history_log(path: str | None = None, limit: int = 100) -> dict:
    from ... import vault_history
    if limit < 1 or limit > 1000:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="limit must be 1..1000")
    try:
        commits = vault_history.log(path=path, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return {
        "path": path,
        "commits": [
            {
                "sha": c.sha,
                "timestamp": c.timestamp,
                "message": c.message,
                "action": c.action,
            }
            for c in commits
        ],
    }


@router.post("/vault/history/undo")
async def vault_history_undo(body: dict) -> dict:
    from ... import vault_history
    path = body.get("path")
    if not isinstance(path, str) or not path:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`path` is required")
    try:
        result = vault_history.undo(path)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return {
        "undone": result.undone,
        "reason": result.reason,
        "commit": result.commit,
        "restored_from": result.restored_from,
        "paths": result.paths or [],
    }


@router.post("/vault/history/purge")
async def vault_history_purge(body: dict | None = None) -> dict:
    from ... import vault_history
    before_iso = (body or {}).get("before_iso") if body else None
    return vault_history.purge(before_iso=before_iso)
