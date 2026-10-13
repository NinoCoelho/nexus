"""HTTP surface for the managed-Chrome computer-use subsystem.

``GET  /browser/instances``            → per-context instance statuses
``GET  /browser/status?key=``          → one instance's status
``POST /browser/action``               → run an action (manual takeover etc.)
``GET  /browser/screenshot?key=``      → live PNG (captures fresh)
``POST /browser/stop``                 → kill one instance's Chrome

When ``[browser] enabled = false`` the manager is never wired onto
``app.state``, so every route here 404s (matches the tool being absent
from the registry).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ...browser.manager import ACTIONS, BrowserError, BrowserManager

router = APIRouter()


def _manager(request: Request) -> BrowserManager:
    manager: BrowserManager | None = getattr(request.app.state, "browser_manager", None)
    if manager is None:
        raise HTTPException(status_code=404, detail="browser disabled in config")
    return manager


class BrowserActionBody(BaseModel):
    # Context key ("main" | "project:<id>"); defaults to "main".
    key: str = "main"
    action: str
    url: str | None = None
    selector: str | None = None
    text: str | None = None
    x: float | None = None
    y: float | None = None
    delta_y: float | None = None
    expression: str | None = None
    timeout_seconds: float | None = None


class BrowserStopBody(BaseModel):
    key: str = "main"


def _check_action(action: str) -> None:
    if action not in ACTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"unknown action {action!r}; expected one of {list(ACTIONS)}",
        )


@router.get("/browser/instances")
async def browser_instances(request: Request) -> list[dict[str, Any]]:
    return await _manager(request).list_instances()


@router.get("/browser/status")
async def browser_status(request: Request, key: str = "main") -> dict[str, Any]:
    return await _manager(request).status(key)


@router.post("/browser/action")
async def browser_action(request: Request, body: BrowserActionBody) -> dict[str, Any]:
    _check_action(body.action)
    args = {
        k: v
        for k, v in {
            "url": body.url,
            "selector": body.selector,
            "text": body.text,
            "x": body.x,
            "y": body.y,
            "delta_y": body.delta_y,
            "expression": body.expression,
            "timeout_seconds": body.timeout_seconds,
        }.items()
        if v is not None
    }
    try:
        # Manual actions run outside any turn — the manager's event sink
        # attributes them to the "*browser*" pseudo-session, which only the
        # global notifications channel carries.
        result = await _manager(request).act(body.key, body.action, args)
    except BrowserError as exc:
        return {"ok": False, "error": str(exc), "key": body.key}
    return {"ok": True, "key": body.key, **result}


@router.get("/browser/screenshot")
async def browser_screenshot(request: Request, key: str = "main") -> FileResponse:
    manager = _manager(request)
    try:
        path = await manager.screenshot(key)
    except BrowserError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return FileResponse(path, media_type="image/png")


@router.post("/browser/stop")
async def browser_stop(request: Request, body: BrowserStopBody) -> dict[str, Any]:
    return await _manager(request).stop(body.key)
