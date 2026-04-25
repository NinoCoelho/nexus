"""Routes for chat (non-streaming): /health, /skills, /chat, /chat/{sid}/* endpoints.

The streaming endpoint POST /chat/stream lives in chat_stream.py,
which imports the shared tracking dicts from this module.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from ..deps import get_agent, get_sessions, get_registry, get_app_state
from ..schemas import (
    ChatReply,
    ChatRequest,
    Health,
    RespondPayload,
    SkillDetail,
    SkillInfo,
)
from ...agent.context import CURRENT_SESSION_ID
from ...agent.llm import LLMTransportError, MalformedOutputError
from ...agent.loop import Agent
from ...skills.registry import SkillRegistry
from ..session_store import SessionStore

log = logging.getLogger(__name__)

router = APIRouter()

# Tracks the in-flight turn's asyncio.Task per session so /chat/{sid}/cancel
# can interrupt a long-running turn. Populated by the chat_stream generator
# on entry; removed in its finally block.
_inflight_turns: dict[str, asyncio.Task[Any]] = {}
# Session ids where /chat/{sid}/cancel was explicitly invoked, so the
# stream generator's CancelledError handler can distinguish a user-clicked
# Stop from a client-disconnect (browser reload / tab close). Both trigger
# ``task.cancel()`` but the persisted status label should differ.
_user_cancelled: set[str] = set()

_trajectory_logger = (
    __import__("nexus.trajectory", fromlist=["TrajectoryLogger"]).TrajectoryLogger()
    if os.environ.get("NEXUS_TRAJECTORIES") == "1"
    else None
)


@router.get("/health", response_model=Health)
async def health() -> Health:
    return Health()


@router.get("/skills", response_model=list[SkillInfo])
async def list_skills(registry: SkillRegistry = Depends(get_registry)) -> list[SkillInfo]:
    return [
        SkillInfo(name=s.name, description=s.description, trust=s.trust)
        for s in registry.list()
    ]


@router.get("/skills/{name}", response_model=SkillDetail)
async def get_skill(name: str, registry: SkillRegistry = Depends(get_registry)) -> SkillDetail:
    try:
        s = registry.get(name)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no such skill: {name!r}")
    return SkillDetail(name=s.name, description=s.description, trust=s.trust, body=s.body)


@router.post("/chat", response_model=ChatReply)
async def chat(
    req: ChatRequest,
    a: Agent = Depends(get_agent),
    store: SessionStore = Depends(get_sessions),
    app_state: dict[str, Any] = Depends(get_app_state),
) -> ChatReply:
    session = store.get_or_create(req.session_id, context=req.context)
    # Bind the session to this request context. Tools that need to
    # address the session (ask_user, trace publish) read it from
    # the ContextVar. Reset on exit so follow-up code — and
    # concurrent unrelated requests — don't inherit stale state.
    token = CURRENT_SESSION_ID.set(session.id)

    cfg = app_state.get("cfg")
    routing_mode = cfg.agent.routing_mode if cfg and cfg.agent else "fixed"

    plan_data: list[dict[str, Any]] | None = None

    try:
        if routing_mode == "planner":
            from .chat_helpers import run_planner_turn
            turn, plan_data = await run_planner_turn(
                agent=a,
                message=req.message,
                session=session,
                cfg=cfg,
                store=store,
                publish_event_fn=store.publish,
            )
        else:
            turn = await a.run_turn(
                req.message,
                history=session.history,
                context=session.context,
            )
    except LLMTransportError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    except MalformedOutputError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    finally:
        CURRENT_SESSION_ID.reset(token)
    store.replace_history(session.id, turn.messages)
    # Fold the turn's usage into the session — see session_store.bump_usage.
    store.bump_usage(
        session.id,
        model=turn.model,
        input_tokens=turn.input_tokens,
        output_tokens=turn.output_tokens,
        tool_calls=turn.tool_calls,
    )
    if _trajectory_logger:
        from .chat_helpers import log_trajectory
        log_trajectory(
            trajectory_logger=_trajectory_logger,
            session_id=session.id,
            turn_index=len(session.history) // 2,
            user_message=req.message,
            history_length=len(session.history),
            context=req.context or "",
            reply=turn.reply or "",
            model=turn.model or "",
            iterations=turn.iterations,
            input_tokens=turn.input_tokens,
            output_tokens=turn.output_tokens,
            tool_calls=turn.tool_calls,
        )
    return ChatReply(
        session_id=session.id,
        reply=turn.reply,
        trace=turn.trace,
        skills_touched=turn.skills_touched,
        iterations=turn.iterations,
        plan=plan_data,
    )


@router.get("/chat/{session_id}/pending")
async def chat_pending(
    session_id: str,
    request: Request,
    store: SessionStore = Depends(get_sessions),
) -> dict[str, Any]:
    """Return the current pending ``ask_user`` request, if any.

    Lets the UI recover a modal that would otherwise be missed if
    the ``/chat/{sid}/events`` EventSource wasn't open at publish
    time (page reload, late subscribe, tab restore). The publish
    bus is fire-and-forget; this endpoint is the authoritative
    snapshot.
    """
    ask_user_handler = request.app.state.ask_user_handler
    requests = store.broker.pending(session_id)
    if not requests:
        return {"pending": None}
    r = requests[0]
    payload: dict[str, Any] = {
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
    return {"pending": payload}


@router.post("/chat/{session_id}/cancel")
async def chat_cancel(
    session_id: str,
    store: SessionStore = Depends(get_sessions),
) -> dict[str, bool]:
    """Interrupt the currently-streaming turn for this session.

    Cancels any pending HITL wait (so ``ask_user`` returns fast) and
    cancels the asyncio Task driving the SSE generator. The client
    will see an ``error`` (reason=cancelled) + ``done`` before the
    stream closes.
    """
    # Unblock any HITL future first so the tool dispatch stops waiting.
    try:
        store.broker.cancel_session(session_id, reason="user_cancelled")
    except Exception:  # noqa: BLE001 — best-effort
        log.exception("broker.cancel_session failed")
    task = _inflight_turns.get(session_id)
    cancelled = False
    if task is not None and not task.done():
        _user_cancelled.add(session_id)
        task.cancel()
        cancelled = True
    return {"ok": True, "cancelled": cancelled}


@router.get("/chat/{session_id}/events")
async def chat_events(session_id: str, store: SessionStore = Depends(get_sessions)) -> StreamingResponse:
    """SSE stream of in-turn events for one session. The UI opens
    this once and receives every ``iter``, ``tool_call``,
    ``tool_result``, ``user_request``, and ``reply`` until the
    client disconnects.

    The subscribe queue is an in-memory pub-sub keyed by session
    id, so the UI can open this stream *before* sending the first
    ``/chat`` message without a chicken-and-egg problem — no DB
    row is required to subscribe. The session is materialized
    lazily by ``POST /chat/stream``. This keeps page-reloads from
    littering the store with empty sessions.

    Note: ``store`` is injected via Depends here. FastAPI's
    dependency resolution on streaming endpoints interacts badly
    with some ASGI transports (the helyx port documented this as
    an ``httpx.ASGITransport`` hang) — direct closure capture is
    the same object at runtime and avoids the pitfall.
    """

    async def stream() -> AsyncIterator[bytes]:
        # Opening comment keeps intermediate proxies from buffering
        # the connection while we wait for the first real event.
        yield b": subscribed\n\n"
        async for event in store.subscribe(session_id):
            yield event.to_sse()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx: don't buffer SSE
        },
    )


@router.post(
    "/chat/{session_id}/respond", status_code=status.HTTP_204_NO_CONTENT
)
async def chat_respond(
    session_id: str,
    body: RespondPayload,
    store: SessionStore = Depends(get_sessions),
) -> None:
    """Resolve a pending ``ask_user`` request. 404 when the request
    is unknown — most commonly because it timed out or the session
    was reset before the user clicked through."""
    resolved = store.resolve_pending(
        session_id, body.request_id, body.answer
    )
    if not resolved:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"no pending request {body.request_id!r} on session "
                f"{session_id!r} (timed out or already resolved)"
            ),
        )
