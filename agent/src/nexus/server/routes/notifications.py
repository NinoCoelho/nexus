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


@router.get("/notifications/pending")
async def notifications_pending(
    request: Request,
    store: SessionStore = Depends(get_sessions),
) -> dict[str, Any]:
    """Snapshot of every pending HITL request across all sessions.

    The publish bus is fire-and-forget; this endpoint lets the UI
    recover any popup that fired while no global subscriber was
    connected (cold tab, hard reload).
    """
    ask_user_handler = request.app.state.ask_user_handler
    items: list[dict[str, Any]] = []
    # broker._requests is private but accessed here the same way
    # ``cancel_pending`` accesses _pending in pubsub.py:82-84.
    for (sid, _rid), r in store.broker._requests.items():
        payload: dict[str, Any] = {
            "session_id": sid,
            "request_id": r.request_id,
            "prompt": r.prompt,
            "kind": r.kind,
            "choices": r.choices,
            "default": r.default,
            "timeout_seconds": r.timeout_seconds,
        }
        extras = ask_user_handler._form_extras.get(r.request_id)
        if extras:
            payload.update(extras)
        items.append(payload)
    return {"pending": items}
