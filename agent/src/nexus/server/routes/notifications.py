"""Cross-session notifications channel.

Two endpoints:

* ``GET /notifications/events`` — SSE stream of HITL ``user_request``
  events from any session, so the UI can surface a single popup
  regardless of which view is active.
* ``GET /notifications/pending`` — authoritative snapshot of every
  pending HITL request across all sessions, used to recover popups
  on page reload (mirrors ``/chat/{sid}/pending`` but global).

Only HITL kinds (``user_request``, ``user_request_auto``,
``user_request_cancelled``) flow through this channel — see the
whitelist in ``session_store.pubsub``. Per-session activity events
(delta, tool_call, tool_result, iter, reply) stay scoped to
``/chat/{sid}/events``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ..deps import get_sessions
from ..session_store import SessionStore

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/notifications/events")
async def notifications_events(
    store: SessionStore = Depends(get_sessions),
) -> StreamingResponse:
    """SSE stream of cross-session HITL events.

    Each event is emitted with its original ``kind`` and a payload
    containing the request data plus a ``session_id`` field so the
    UI can route the answer back via ``/chat/{session_id}/respond``.
    """

    async def stream() -> AsyncIterator[bytes]:
        yield b": subscribed\n\n"
        async for session_id, event in store.subscribe_global():
            payload = {"session_id": session_id, **event.data}
            yield f"event: {event.kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/notifications/history")
async def notifications_history(
    limit: int = 50,
    store: SessionStore = Depends(get_sessions),
) -> dict[str, Any]:
    """Recent HITL events with status, for the bell dropdown.

    Survives daemon restart (rows are persisted), so a prompt that
    timed out while the user was AFK is still visible. Pruned to the
    last 200 rows / last 7 days on each insert burst (see
    ``trim_hitl_events``); the UI's ``limit`` is just a page size on
    top of that retention.
    """
    return {"history": store.list_hitl_events(limit=limit)}


@router.get("/notifications/pending")
async def notifications_pending(
    request: Request,
    store: SessionStore = Depends(get_sessions),
) -> dict[str, Any]:
    """Snapshot of every pending HITL request across all sessions.

    Two sources, UNION'd by ``request_id``:

    1. ``broker._requests`` — live in-memory requests where the agent's
       turn is still waiting. Lost on restart.
    2. ``hitl_pending`` (status='parked') — durable rows for requests
       that exhausted the synchronous wait threshold. Survive restart.

    Live entries win over parked entries when a request_id appears in
    both (the agent reactivated after a restart, e.g. via /respond
    arriving while a parked row still exists transiently).
    """
    ask_user_handler = request.app.state.ask_user_handler
    seen: set[str] = set()
    items: list[dict[str, Any]] = []

    # broker._requests is private but accessed here the same way
    # ``cancel_pending`` accesses _pending in pubsub.py.
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
        extras = ask_user_handler._form_extras.get(r.request_id)
        if extras:
            payload.update(extras)
        items.append(payload)

    for row in store.list_all_pending():
        rid = row["request_id"]
        if rid in seen:
            continue
        items.append({
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
        })

    return {"pending": items}
