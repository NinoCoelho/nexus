"""Routes for chat (non-streaming): /health, /skills, /chat, /chat/{sid}/* endpoints.

The streaming endpoint POST /chat/stream lives in chat_stream.py,
which imports the shared tracking dicts from this module.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from pydantic import BaseModel

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
from ...skills.manager import SkillManager
from ...skills.registry import SkillRegistry
from ..session_store import SessionStore


class SkillUpdate(BaseModel):
    body: str

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


@router.put("/skills/{name}", response_model=SkillDetail)
async def update_skill(
    name: str,
    payload: SkillUpdate,
    registry: SkillRegistry = Depends(get_registry),
) -> SkillDetail:
    """Replace a skill's SKILL.md body. Runs the same guard scan as the
    agent's skill_manage tool; returns 400 if the new content is rejected
    or fails frontmatter validation."""
    try:
        registry.get(name)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no such skill: {name!r}")
    manager = SkillManager(registry)
    result = manager.invoke("edit", {"name": name, "content": payload.body})
    if not result.ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result.message)
    s = registry.get(name)
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

    plan_data: list[dict[str, Any]] | None = None

    try:
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
    was reset before the user clicked through.

    For requests that have *parked* (the agent ended the turn waiting
    for an async answer), this endpoint returns 409 with the parked
    request id; the UI must resume via
    ``POST /chat/{session_id}/hitl/{request_id}/answer``.
    """
    parked = store.get_hitl_pending(body.request_id)
    if parked is not None and parked.get("status") == "parked":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "parked",
                "request_id": body.request_id,
                "session_id": parked.get("session_id"),
                "message": (
                    "request is parked; answer via "
                    f"/chat/{parked.get('session_id')}/hitl/"
                    f"{body.request_id}/answer to resume the turn"
                ),
            },
        )
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


@router.post("/chat/{session_id}/hitl/{request_id}/answer")
async def chat_hitl_answer(
    session_id: str,
    request_id: str,
    body: RespondPayload,
    a: Agent = Depends(get_agent),
    store: SessionStore = Depends(get_sessions),
) -> StreamingResponse:
    """Resume a parked ``ask_user`` request and stream the agent's
    continuation as SSE.

    Two outcomes:

    1. **Idempotent duplicate** — another client already answered. Returns
       the recorded answer in a single ``done`` SSE frame and closes.
    2. **First answer wins** — marks the row answered, decodes the answer
       into a tool result, drives ``Agent.continue_after_hitl`` and forwards
       events identically to ``/chat/stream``.

    A 404 means the request was never parked (timed out without parking,
    or already cleaned up).
    """
    if request_id != body.request_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path request_id and body.request_id must match",
        )

    parked = store.get_hitl_pending(request_id)
    if parked is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no parked request {request_id!r}",
        )
    if parked.get("session_id") != session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="request_id belongs to a different session",
        )

    # Decode the answer the same way ask_user_tool decodes broker answers
    # (form payloads come in as JSON strings; everything else as plain text).
    raw_answer = body.answer
    decoded: Any = raw_answer
    if isinstance(raw_answer, str):
        try:
            decoded = json.loads(raw_answer)
        except (json.JSONDecodeError, ValueError):
            decoded = raw_answer

    row = store.mark_hitl_pending_answered(request_id, decoded)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no parked request {request_id!r}",
        )

    already_answered = bool(row.get("already_answered"))

    async def event_generator() -> AsyncIterator[str]:
        if already_answered:
            # Idempotent duplicate — emit a synthetic done with the prior
            # answer so the second caller can unwind its UI cleanly.
            payload = {
                "session_id": session_id,
                "reply": "",
                "duplicate": True,
                "answer": row.get("answer_json"),
            }
            yield f"event: done\ndata: {json.dumps(payload)}\n\n"
            return

        token = CURRENT_SESSION_ID.set(session_id)
        current = asyncio.current_task()
        if current is not None:
            _inflight_turns[session_id] = current
        accumulated_text = ""
        accumulated_tools: list[dict[str, Any]] = []
        final_messages = None
        partial_status = "interrupted"
        try:
            async for event in a.continue_after_hitl(
                session_id=session_id,
                request_id=request_id,
                answer=decoded,
            ):
                etype = event.get("type")
                if etype == "delta":
                    accumulated_text += event.get("text", "")
                    yield (
                        f"event: delta\ndata: "
                        f"{json.dumps({'text': event['text']})}\n\n"
                    )
                elif etype == "thinking":
                    yield (
                        f"event: thinking\ndata: "
                        f"{json.dumps({'text': event.get('text', '')})}\n\n"
                    )
                elif etype in ("tool_exec_start", "tool_exec_result"):
                    payload = {"name": event.get("name", "")}
                    if "args" in event:
                        payload["args"] = event["args"]
                    if "result_preview" in event:
                        payload["result_preview"] = event["result_preview"]
                    if etype == "tool_exec_start":
                        accumulated_tools.append({
                            "name": event.get("name", ""),
                            "args": event.get("args"),
                            "status": "pending",
                        })
                    else:
                        for t in reversed(accumulated_tools):
                            if (
                                t.get("name") == event.get("name")
                                and t.get("status") == "pending"
                            ):
                                t["status"] = "done"
                                t["result_preview"] = event.get("result_preview")
                                break
                    yield f"event: tool\ndata: {json.dumps(payload)}\n\n"
                elif etype == "limit_reached":
                    yield (
                        f"event: limit_reached\ndata: "
                        f"{json.dumps({'iterations': event.get('iterations', 0)})}\n\n"
                    )
                elif etype == "done":
                    final_messages = event.get("messages")
                    usage = event.get("usage") or {}
                    try:
                        store.bump_usage(
                            session_id,
                            model=usage.get("model"),
                            input_tokens=int(usage.get("input_tokens") or 0),
                            output_tokens=int(usage.get("output_tokens") or 0),
                            tool_calls=int(usage.get("tool_calls") or 0),
                        )
                    except Exception:  # noqa: BLE001
                        log.exception("bump_usage failed (resume)")
                    done_payload = {
                        "session_id": event.get("session_id") or session_id,
                        "reply": event.get("reply", ""),
                        "trace": event.get("trace", []),
                        "skills_touched": event.get("skills_touched", []),
                        "iterations": event.get("iterations", 0),
                        "usage": usage,
                        "model": usage.get("model"),
                    }
                    yield f"event: done\ndata: {json.dumps(done_payload)}\n\n"
                elif etype == "error":
                    err_payload = {
                        "detail": event.get("detail", ""),
                        "reason": event.get("reason"),
                        "retryable": event.get("retryable"),
                        "status_code": event.get("status_code"),
                    }
                    yield f"event: error\ndata: {json.dumps(err_payload)}\n\n"
        except (LLMTransportError, MalformedOutputError) as exc:
            partial_status = "llm_error"
            yield (
                f"event: error\ndata: "
                f"{json.dumps({'detail': str(exc)})}\n\n"
            )
        except asyncio.CancelledError:
            partial_status = "cancelled"
            yield (
                f"event: error\ndata: "
                f"{json.dumps({'detail': 'cancelled by user', 'reason': 'cancelled'})}\n\n"
            )
            yield (
                f"event: done\ndata: "
                f"{json.dumps({'session_id': session_id, 'reply': ''})}\n\n"
            )
        except Exception as exc:  # noqa: BLE001
            partial_status = "crashed"
            log.exception("hitl resume crashed")
            yield (
                f"event: error\ndata: "
                f"{json.dumps({'detail': f'{type(exc).__name__}: {exc}'})}\n\n"
            )
            yield (
                f"event: done\ndata: "
                f"{json.dumps({'session_id': session_id, 'reply': ''})}\n\n"
            )
        finally:
            if final_messages is not None:
                try:
                    store.replace_history(session_id, final_messages)
                except Exception:  # noqa: BLE001
                    log.exception("replace_history (resume) failed")
            elif accumulated_text or accumulated_tools:
                try:
                    sess = store.get(session_id)
                    base = list(sess.history) if sess else []
                    store.persist_partial_turn(
                        session_id,
                        base_history=base,
                        user_message="",  # resumed turn has no fresh user msg
                        assistant_text=accumulated_text,
                        tool_calls=accumulated_tools,
                        status_note=partial_status,
                    )
                except Exception:  # noqa: BLE001
                    log.exception("persist_partial_turn (resume) failed")
            CURRENT_SESSION_ID.reset(token)
            if _inflight_turns.get(session_id) is current:
                _inflight_turns.pop(session_id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
