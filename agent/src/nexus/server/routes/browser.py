"""HTTP surface for the managed-Chrome computer-use subsystem.

``GET  /browser/instances``            → per-context instance statuses
``GET  /browser/status?key=``          → one instance's status
``POST /browser/action``               → run an action (manual takeover etc.)
``GET  /browser/screenshot?key=``      → live PNG (captures fresh)
``GET  /browser/stream?key=``          → SSE live view (screencast frames)
``POST /browser/stop``                 → kill one instance's Chrome

When ``[browser] enabled = false`` the manager is never wired onto
``app.state``, so every route here 404s (matches the tool being absent
from the registry).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from ...browser.manager import ACTIONS, CONTROL_ACTIONS, BrowserError, BrowserManager

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
    # Normalized (0..1) viewport fractions — the remote-control coordinate
    # space (see BrowserInstance._act_tap).
    x_norm: float | None = None
    y_norm: float | None = None
    # Special key name for action="key" (enter/backspace/arrowup/…).
    key_name: str | None = None
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


def _require_remote_control(request: Request, action: str) -> None:
    """Gate state-changing actions from non-loopback clients.

    The live stream stays watch-only; ``POST /browser/action`` is the
    sanctioned control channel for paired native clients — but only when
    ``[browser].remote_control`` is opted in. Loopback clients (the desktop
    UI, CLI) are never restricted.
    """
    if action not in CONTROL_ACTIONS:
        return
    client_host = request.client.host if request.client else ""
    if client_host in ("127.0.0.1", "::1", "localhost"):
        return
    cfg = request.app.state.mutable_state["cfg"]
    if not getattr(getattr(cfg, "browser", None), "remote_control", False):
        raise HTTPException(
            status_code=403,
            detail=(
                "remote browser control is disabled on this server — enable it "
                "with [browser] remote_control = true in ~/.nexus/config.toml"
            ),
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
    _require_remote_control(request, body.action)
    args = {
        k: v
        for k, v in {
            "url": body.url,
            "selector": body.selector,
            "text": body.text,
            "x": body.x,
            "y": body.y,
            "x_norm": body.x_norm,
            "y_norm": body.y_norm,
            "key": body.key_name,
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


@router.get("/browser/stream")
async def browser_stream(request: Request, key: str = "main") -> StreamingResponse:
    """SSE live view of one context's browser (CDP screencast).

    Events: ``frame`` (base64 JPEG), ``info`` (url/title + viewport
    width/height for remote tap mapping), ``heartbeat``, ``error``
    (terminal — e.g. Chrome still starting; the client may reconnect to
    retry). The stream itself stays watch-only by design; interaction goes
    through ``POST /browser/action`` (``tap``/``scroll``/``type``/``key``),
    which loopback may always use and remote clients only with
    ``[browser].remote_control``.
    """
    manager = _manager(request)

    async def gen():
        try:
            async for ev in manager.stream(key):
                yield f"data: {json.dumps(ev)}\n\n"
        except BrowserError as exc:
            yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/browser/stop")
async def browser_stop(request: Request, body: BrowserStopBody) -> dict[str, Any]:
    # Killing a bot's browser is a control action — same remote gate.
    _require_remote_control(request, "click")
    return await _manager(request).stop(body.key)
