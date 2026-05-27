"""Admin routes for cross-user HITL approval and resource ACL management.

All endpoints require admin role.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..session_store import SessionStore
from ..auth import CurrentUser
from ...server.user_store.models import User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")


def _require_admin(request: Request) -> User:
    user = CurrentUser(optional=True)(request)
    if not user or user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin required")
    return user


@router.get("/pending")
async def admin_all_pending(request: Request, _admin: User = Depends(_require_admin)) -> dict[str, Any]:
    ask_user_handler = request.app.state.ask_user_handler
    items: list[dict[str, Any]] = []

    if not getattr(request.app.state, "multi_user", False):
        store = request.app.state.sessions
        _collect_pending(store, ask_user_handler, items, None)
    else:
        registry = request.app.state.session_registry
        user_store = request.app.state.user_store
        for user in user_store.list_users():
            if user.status != "active":
                continue
            store = registry.get(user.id)
            _collect_pending(store, ask_user_handler, items, user)

    return {"pending": items}


def _collect_pending(
    store: SessionStore,
    ask_user_handler: Any,
    items: list[dict[str, Any]],
    user: User | None,
) -> None:
    seen: set[str] = set()
    for (sid, _rid), r in store.broker._requests.items():
        seen.add(r.request_id)
        payload: dict[str, Any] = {
            "session_id": sid,
            "request_id": r.request_id,
            "prompt": r.prompt,
            "kind": r.kind,
            "choices": r.choices,
            "default": r.default,
            "timeout_seconds": r.timeout_seconds,
            "status": "live",
        }
        if user:
            payload["user_id"] = user.id
            payload["user_name"] = user.display_name
        extras = ask_user_handler._form_extras.get(r.request_id)
        if extras:
            payload.update(extras)
        items.append(payload)

    for row in store.list_all_pending():
        rid = row["request_id"]
        if rid in seen:
            continue
        entry: dict[str, Any] = {
            "session_id": row["session_id"],
            "request_id": rid,
            "prompt": row["prompt"],
            "kind": row["kind"],
            "choices": row.get("choices"),
            "default": row.get("default"),
            "timeout_seconds": row.get("timeout_seconds"),
            "fields": row.get("fields"),
            "form_title": row.get("form_title"),
            "form_description": row.get("form_description"),
            "status": "parked",
            "created_at": row.get("created_at"),
        }
        if user:
            entry["user_id"] = user.id
            entry["user_name"] = user.display_name
        items.append(entry)


@router.post("/hitl/{session_id}/{request_id}/answer")
async def admin_hitl_answer(
    session_id: str,
    request_id: str,
    request: Request,
    _admin: User = Depends(_require_admin),
) -> dict[str, Any]:
    store = _resolve_store_for_session(request, session_id)

    parked = store.get_hitl_pending(request_id)
    if parked is not None and parked.get("status") == "parked":
        answer = (await request.json()).get("answer", "yes")
        raw = answer
        decoded: Any = raw
        if isinstance(raw, str):
            try:
                decoded = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                decoded = raw
        row = store.mark_hitl_pending_answered(request_id, decoded)
        if row is None:
            raise HTTPException(status_code=404, detail="parked request not found")
        return {"status": "answered", "request_id": request_id}

    resolved = store.resolve_pending(session_id, request_id, (await request.json()).get("answer", "yes"))
    if not resolved:
        raise HTTPException(
            status_code=404,
            detail=f"no pending request {request_id!r} on session {session_id!r}",
        )
    return {"status": "resolved", "request_id": request_id}


@router.post("/hitl/{session_id}/{request_id}/cancel")
async def admin_hitl_cancel(
    session_id: str,
    request_id: str,
    request: Request,
    _admin: User = Depends(_require_admin),
) -> dict[str, Any]:
    store = _resolve_store_for_session(request, session_id)
    store.cancel_pending(session_id, request_id)
    return {"status": "cancelled", "request_id": request_id}


def _resolve_store_for_session(request: Request, session_id: str) -> SessionStore:
    if not getattr(request.app.state, "multi_user", False):
        return request.app.state.sessions

    registry = request.app.state.session_registry
    user_store = request.app.state.user_store
    owner_store = registry.store_for_session(session_id, user_store)
    if owner_store:
        return owner_store

    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return registry.get(user_id)
    return request.app.state.sessions
