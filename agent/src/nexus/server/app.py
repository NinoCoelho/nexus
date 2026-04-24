"""FastAPI application factory for Nexus."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from ..agent.ask_user_tool import AskUserHandler
from ..agent.context import CURRENT_SESSION_ID
from ..agent.llm import LLMTransportError, MalformedOutputError
from ..agent.loop import Agent
from ..skills.registry import SkillRegistry
from .events import SessionEvent
from .schemas import (
    ChatReply,
    ChatRequest,
    Health,
    ModelRolePayload,
    RespondPayload,
    SettingsPayload,
    SkillDetail,
    SkillInfo,
    TruncateRequest,
)
from .session_store import SessionStore
from .settings import SettingsStore

log = logging.getLogger(__name__)


from ..trajectory import TrajectoryLogger

_trajectory_logger: TrajectoryLogger | None = (
    TrajectoryLogger() if os.environ.get("NEXUS_TRAJECTORIES") == "1" else None
)

# Tracks the in-flight turn's asyncio.Task per session so /chat/{sid}/cancel
# can interrupt a long-running turn. Populated by the chat_stream generator
# on entry; removed in its finally block.
_inflight_turns: dict[str, asyncio.Task[Any]] = {}
# Session ids where /chat/{sid}/cancel was explicitly invoked, so the
# stream generator's CancelledError handler can distinguish a user-clicked
# Stop from a client-disconnect (browser reload / tab close). Both trigger
# ``task.cancel()`` but the persisted status label should differ.
_user_cancelled: set[str] = set()

_graphrag_index_tasks: dict[str, dict[str, Any]] = {}


def create_app(
    *,
    agent: Agent,
    registry: SkillRegistry,
    sessions: SessionStore | None = None,
    nexus_cfg: Any | None = None,
    provider_registry: Any | None = None,
    settings_store: SettingsStore | None = None,
    graphrag_cfg: Any | None = None,
) -> FastAPI:
    sessions = sessions or SessionStore()
    settings_store = settings_store or SettingsStore()
    _state = {"cfg": nexus_cfg, "prov_reg": provider_registry}

    # Wire the HITL primitive. The ``AskUserHandler`` reads ``yolo_mode``
    # on every call via this getter — a callable (not a snapshot) so
    # toggling the setting takes effect on the next ``ask_user`` without
    # restarting the server. Attached to the agent so its loop's
    # ``_tools()`` / ``_handle()`` branches pick it up.
    ask_user_handler = AskUserHandler(
        session_store=sessions,
        yolo_mode_getter=lambda: settings_store.get().yolo_mode,
    )
    # Late-bind the handler onto the agent. Constructed-outside-the-app
    # callers (``main.py``) don't know about HITL; constructing the
    # handler here keeps all the server-side wiring in one place.
    from ..agent.terminal_tool import TerminalHandler

    agent._ask_user_handler = ask_user_handler
    agent._terminal_handler = TerminalHandler(ask_user_handler=ask_user_handler)

    # Trace callback routes every agent event (iter, tool_call,
    # tool_result, reply) into the SSE subscriber fanout for whichever
    # session is currently running the turn. Reads the session_id from
    # a contextvar set in the /chat handler — the Agent stays
    # session-agnostic.
    def _trace(kind: str, data: dict[str, Any]) -> None:
        session_id = CURRENT_SESSION_ID.get()
        if session_id is None:
            return
        sessions.publish(session_id, SessionEvent(kind=kind, data=data))

    # Install the trace hook without clobbering one the caller may
    # already have wired (main.py doesn't today, but a test might).
    if agent._trace is None:
        agent._trace = _trace
    else:
        existing = agent._trace
        def _compose(k: str, d: dict[str, Any]) -> None:
            existing(k, d)
            _trace(k, d)
        agent._trace = _compose

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        log.info("Lifespan starting (graphrag_cfg=%s)", "present" if graphrag_cfg is not None else "None")
        if graphrag_cfg is not None:
            try:
                from ..agent.graphrag_manager import initialize

                log.info("Initializing GraphRAG engine...")
                await initialize(nexus_cfg)
                log.info("GraphRAG engine initialized")
            except Exception:
                log.exception("GraphRAG initialization failed")
        try:
            yield
        finally:
            from ..agent.memory import close_memory_store
            close_memory_store()
            await agent.aclose()

    app = FastAPI(title="nexus", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://localhost:\d+",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def get_agent() -> Agent:
        return agent

    def get_sessions() -> SessionStore:
        return sessions

    # ── existing routes ────────────────────────────────────────────────────────

    @app.get("/health", response_model=Health)
    async def health() -> Health:
        return Health()

    @app.get("/skills", response_model=list[SkillInfo])
    async def list_skills() -> list[SkillInfo]:
        return [
            SkillInfo(name=s.name, description=s.description, trust=s.trust)
            for s in registry.list()
        ]

    @app.get("/skills/{name}", response_model=SkillDetail)
    async def get_skill(name: str) -> SkillDetail:
        try:
            s = registry.get(name)
        except KeyError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no such skill: {name!r}")
        return SkillDetail(name=s.name, description=s.description, trust=s.trust, body=s.body)

    @app.post("/chat", response_model=ChatReply)
    async def chat(
        req: ChatRequest,
        a: Agent = Depends(get_agent),
        store: SessionStore = Depends(get_sessions),
    ) -> ChatReply:
        from ..agent.planner import PlannerAgent

        session = store.get_or_create(req.session_id, context=req.context)
        # Bind the session to this request context. Tools that need to
        # address the session (ask_user, trace publish) read it from
        # the ContextVar. Reset on exit so follow-up code — and
        # concurrent unrelated requests — don't inherit stale state.
        token = CURRENT_SESSION_ID.set(session.id)

        cfg = _state.get("cfg")
        routing_mode = cfg.agent.routing_mode if cfg and cfg.agent else "fixed"

        plan_data: list[dict[str, Any]] | None = None

        try:
            if routing_mode == "planner":
                trace_events: list[dict[str, Any]] = []

                def _on_planner_trace(event: dict[str, Any]) -> None:
                    trace_events.append(event)
                    # Also forward into the SSE channel so subscribers see plan events
                    _trace(event.get("type", "plan_event"), {k: v for k, v in event.items() if k != "type"})

                default_model = cfg.agent.default_model if cfg and cfg.agent else None
                provider, _ = a._resolve_provider(default_model)
                planner = PlannerAgent(
                    executor=a,
                    llm=provider,
                    planner_model=None,
                    on_trace=_on_planner_trace,
                )
                result = await planner.run_turn(
                    req.message,
                    history=session.history,
                    context=session.context,
                )
                reply_text = result.reply
                plan_data = [
                    {
                        "id": st.id,
                        "description": st.description,
                        "status": st.status,
                        "result_preview": (st.result or "")[:200],
                    }
                    for st in result.sub_tasks
                ] or None
                # Build a minimal AgentTurn-like object for history/usage purposes
                from ..agent.loop import AgentTurn
                from ..agent.llm import ChatMessage, Role
                extra_msg = ChatMessage(role=Role.ASSISTANT, content=reply_text)
                turn_messages = list(session.history) + [
                    ChatMessage(role=Role.USER, content=req.message),
                    extra_msg,
                ]
                turn = AgentTurn(
                    reply=reply_text,
                    skills_touched=[],
                    iterations=1,
                    trace=trace_events,
                    messages=turn_messages,
                    input_tokens=0,
                    output_tokens=0,
                    tool_calls=0,
                    model=default_model,
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
            try:
                _trajectory_logger.log(
                    session_id=session.id,
                    turn_index=len(session.history) // 2,
                    state={
                        "user_message": req.message,
                        "history_length": len(session.history),
                        "context": (req.context or "")[:200],
                    },
                    action={
                        "reply": turn.reply[:2000] if turn.reply else "",
                        "model": turn.model if turn.model else "",
                        "iterations": turn.iterations,
                        "tool_calls": [],
                        "input_tokens": turn.input_tokens,
                        "output_tokens": turn.output_tokens,
                    },
                    reward={
                        "explicit": None,
                        "implicit": {
                            "turn_completed": True,
                            "tool_call_count": turn.tool_calls,
                        },
                    },
                )
            except Exception:  # noqa: BLE001 — best-effort
                log.exception("trajectory logging failed")
        return ChatReply(
            session_id=session.id,
            reply=turn.reply,
            trace=turn.trace,
            skills_touched=turn.skills_touched,
            iterations=turn.iterations,
            plan=plan_data,
        )

    @app.post("/chat/stream")
    async def chat_stream_route(
        req: ChatRequest,
        a: Agent = Depends(get_agent),
        store: SessionStore = Depends(get_sessions),
    ) -> StreamingResponse:

        session = store.get_or_create(req.session_id, context=req.context)

        cfg = _state.get("cfg")

        # Resolve routing: explicit `routing_mode` wins; legacy "auto" sentinel
        # in `model` still accepted. Default: fixed.
        routing_mode = (getattr(req, "routing_mode", None) or "").lower()
        if not routing_mode:
            routing_mode = "auto" if req.model == "auto" else "fixed"
        resolved_model_id = req.model if req.model and req.model != "auto" else ""
        routed_by = "user"
        if routing_mode == "auto":
            from ..agent.router import classify_route
            resolved_model_id = await classify_route(
                req.message, cfg, _state.get("prov_reg"),
            )
            routed_by = "auto"

        async def event_generator() -> AsyncIterator[str]:
            final_messages = None
            # Snapshot the pre-turn history so partial-persistence on
            # abnormal exit can rebuild "history + user + partial assistant"
            # without double-appending.
            pre_turn_history = list(session.history)
            accumulated_text = ""
            accumulated_tools: list[dict[str, Any]] = []
            partial_status = "interrupted"
            # Bind session context for the duration of the stream so
            # any ask_user call inside the turn knows which session to
            # publish on. The contextvar is local to this coroutine
            # (generators carry their own context) so concurrent
            # streams don't stomp on each other.
            token = CURRENT_SESSION_ID.set(session.id)
            current = asyncio.current_task()
            if current is not None:
                _inflight_turns[session.id] = current
            # Eagerly persist the user message so a crash between POST
            # and the first delta doesn't lose the prompt the user typed.
            try:
                from ..agent.llm import ChatMessage as _CM, Role as _R
                store.replace_history(
                    session.id,
                    pre_turn_history + [_CM(role=_R.USER, content=req.message)],
                )
            except Exception:  # noqa: BLE001 — best-effort
                log.exception("pre-turn user message persist failed")
            try:
                async for event in a.run_turn_stream(
                    req.message,
                    history=session.history,
                    context=session.context,
                    session_id=session.id,
                    model_id=resolved_model_id,
                ):
                    etype = event.get("type")

                    if etype == "delta":
                        accumulated_text += event.get("text", "")
                        yield f"event: delta\ndata: {json.dumps({'text': event['text']})}\n\n"

                    elif etype == "limit_reached":
                        partial_status = "iteration_limit"
                        yield f"event: limit_reached\ndata: {json.dumps({'iterations': event.get('iterations', 0)})}\n\n"

                    elif etype in ("tool_exec_start", "tool_exec_result"):
                        payload: dict[str, Any] = {"name": event.get("name", "")}
                        if "args" in event:
                            payload["args"] = event["args"]
                        if "result_preview" in event:
                            payload["result_preview"] = event["result_preview"]
                        # Keep a running tool trace so a mid-turn abort still
                        # leaves badges in persisted history.
                        if etype == "tool_exec_start":
                            accumulated_tools.append({
                                "name": event.get("name", ""),
                                "args": event.get("args"),
                                "status": "pending",
                            })
                        else:
                            for t in reversed(accumulated_tools):
                                if t.get("name") == event.get("name") and t.get("status") == "pending":
                                    t["status"] = "done"
                                    t["result_preview"] = event.get("result_preview")
                                    break
                        yield f"event: tool\ndata: {json.dumps(payload)}\n\n"

                    elif etype == "done":
                        final_messages = event.get("messages")
                        usage = event.get("usage") or {}
                        # Persist the turn's usage onto the session so
                        # /insights can roll it up later. Done here (not
                        # in `finally`) because the done event is only
                        # emitted on successful completion of the turn.
                        try:
                            store.bump_usage(
                                session.id,
                                model=usage.get("model"),
                                input_tokens=int(usage.get("input_tokens") or 0),
                                output_tokens=int(usage.get("output_tokens") or 0),
                                tool_calls=int(usage.get("tool_calls") or 0),
                            )
                        except Exception:  # noqa: BLE001 — best-effort
                            log.exception("bump_usage failed")
                        if _trajectory_logger:
                            try:
                                reply_text = event.get("reply", "")
                                _trajectory_logger.log(
                                    session_id=session.id,
                                    turn_index=len(session.history) // 2,
                                    state={
                                        "user_message": req.message,
                                        "history_length": len(session.history),
                                        "context": (req.context or "")[:200],
                                    },
                                    action={
                                        "reply": reply_text[:2000] if reply_text else "",
                                        "model": usage.get("model") or "",
                                        "iterations": event.get("iterations", 0),
                                        "tool_calls": [],
                                        "input_tokens": int(usage.get("input_tokens") or 0),
                                        "output_tokens": int(usage.get("output_tokens") or 0),
                                    },
                                    reward={
                                        "explicit": None,
                                        "implicit": {
                                            "turn_completed": True,
                                            "tool_call_count": int(usage.get("tool_calls") or 0),
                                        },
                                    },
                                )
                            except Exception:  # noqa: BLE001 — best-effort
                                log.exception("trajectory logging failed (stream)")
                        done_payload = {
                            "session_id": event.get("session_id") or session.id,
                            "reply": event.get("reply", ""),
                            "trace": event.get("trace", []),
                            "skills_touched": event.get("skills_touched", []),
                            "iterations": event.get("iterations", 0),
                            "usage": usage,
                            "model": usage.get("model") or resolved_model_id,
                            "routed_by": routed_by,
                        }
                        yield f"event: done\ndata: {json.dumps(done_payload)}\n\n"

                    elif etype == "error":
                        # Mid-stream structured error from the agent loop
                        # (e.g. an upstream failure after content was already
                        # streamed, so retry was impossible, or a truncation /
                        # empty-response signal emitted by loop.py before the
                        # done terminator). Forward the classifier's fields so
                        # the UI can show a richer message without re-parsing
                        # the detail string — and stamp partial_status so the
                        # persisted assistant prefix matches the banner.
                        reason = event.get("reason")
                        if reason in ("length", "empty_response", "upstream_timeout"):
                            partial_status = reason
                        err_payload = {
                            "detail": event.get("detail", ""),
                            "reason": reason,
                            "retryable": event.get("retryable"),
                            "status_code": event.get("status_code"),
                        }
                        yield f"event: error\ndata: {json.dumps(err_payload)}\n\n"
            except (LLMTransportError, MalformedOutputError) as exc:
                # Map loom's classified reason (timeout / rate_limit / 5xx /
                # auth / ...) onto our partial_status so the persisted prefix
                # + UI banner line up. Fall back to llm_error for anything
                # the classifier doesn't recognise.
                partial_status = "llm_error"
                try:
                    from ..error_classifier import classify_api_error as _c
                    _reason = _c(exc).reason.value
                    if _reason == "timeout":
                        partial_status = "upstream_timeout"
                except Exception:  # noqa: BLE001
                    pass
                # Classify so the client gets a readable summary on top of
                # the raw detail (e.g. "Provider rate limit — retrying with
                # backoff." vs. the raw "HTTP 429: ..." body).
                detail = str(exc)
                reason = None
                retryable = None
                status_code = getattr(exc, "status_code", None)
                try:
                    from ..error_classifier import classify_api_error
                    classified = classify_api_error(exc)
                    reason = classified.reason.value
                    retryable = classified.retryable
                    if classified.user_facing_summary:
                        detail = f"{classified.user_facing_summary} ({detail})"
                except Exception:
                    pass
                err_payload = {
                    "detail": detail,
                    "reason": reason,
                    "retryable": retryable,
                    "status_code": status_code,
                }
                yield f"event: error\ndata: {json.dumps(err_payload)}\n\n"
            except asyncio.CancelledError:
                partial_status = "cancelled" if session.id in _user_cancelled else "interrupted"
                _user_cancelled.discard(session.id)
                yield f"event: error\ndata: {json.dumps({'detail': 'cancelled by user', 'reason': 'cancelled'})}\n\n"
                yield f"event: done\ndata: {json.dumps({'session_id': session.id, 'reply': '', 'trace': [], 'skills_touched': [], 'iterations': 0})}\n\n"
            except Exception as exc:
                partial_status = "crashed"
                # Catch-all so an unexpected error never leaves the client
                # with ERR_INCOMPLETE_CHUNKED_ENCODING. Emit a proper
                # error frame then a terminator done so the client can
                # unwind its UI (flip thinking off, show the error).
                log.exception("chat_stream crashed")
                yield f"event: error\ndata: {json.dumps({'detail': f'{type(exc).__name__}: {exc}'})}\n\n"
                yield f"event: done\ndata: {json.dumps({'session_id': session.id, 'reply': '', 'trace': [], 'skills_touched': [], 'iterations': 0})}\n\n"
            finally:
                if final_messages is not None and partial_status in (
                    "length", "empty_response", "upstream_timeout",
                ):
                    # Loom still delivered a final message list, but the turn
                    # was truncated / empty / timed out. Stamp the status
                    # prefix onto the persisted assistant so the UI renders a
                    # Retry/Continue banner on reload. Falls back to the
                    # partial-turn writer which knows how to prefix content.
                    try:
                        # Find the trailing assistant message in final_messages
                        # and use its text + tool_calls as the partial state.
                        last_asst_text = ""
                        last_asst_tools: list[dict[str, Any]] = []
                        for m in reversed(final_messages):
                            if getattr(m, "role", None) and m.role.value == "assistant":
                                last_asst_text = m.content or ""
                                if m.tool_calls:
                                    last_asst_tools = [
                                        {
                                            "id": tc.id,
                                            "name": tc.name,
                                            "args": tc.arguments,
                                            "status": "done",
                                        }
                                        for tc in m.tool_calls
                                    ]
                                break
                        store.persist_partial_turn(
                            session.id,
                            base_history=pre_turn_history,
                            user_message=req.message,
                            assistant_text=last_asst_text,
                            tool_calls=last_asst_tools,
                            status_note=partial_status,
                        )
                    except Exception:  # noqa: BLE001 — best-effort
                        log.exception("status-stamped partial persist failed")
                        store.replace_history(session.id, final_messages)
                elif final_messages is not None:
                    store.replace_history(session.id, final_messages)
                else:
                    # Stream didn't reach a `done` event — persist whatever
                    # we accumulated so a reload can see the partial reply
                    # and the tool badges that were already executed. This
                    # is what makes the UI recover gracefully after a
                    # server restart, a cancel, an LLM timeout, or a loop
                    # limit hit.
                    try:
                        store.persist_partial_turn(
                            session.id,
                            base_history=pre_turn_history,
                            user_message=req.message,
                            assistant_text=accumulated_text,
                            tool_calls=accumulated_tools,
                            status_note=partial_status,
                        )
                    except Exception:  # noqa: BLE001 — best-effort
                        log.exception("partial turn persist failed")
                CURRENT_SESSION_ID.reset(token)
                if _inflight_turns.get(session.id) is current:
                    _inflight_turns.pop(session.id, None)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/chat/{session_id}/pending")
    async def chat_pending(
        session_id: str,
        store: SessionStore = Depends(get_sessions),
    ) -> dict[str, Any]:
        """Return the current pending ``ask_user`` request, if any.

        Lets the UI recover a modal that would otherwise be missed if
        the ``/chat/{sid}/events`` EventSource wasn't open at publish
        time (page reload, late subscribe, tab restore). The publish
        bus is fire-and-forget; this endpoint is the authoritative
        snapshot.
        """
        requests = store.broker.pending(session_id)
        if not requests:
            return {"pending": None}
        r = requests[0]
        return {
            "pending": {
                "request_id": r.request_id,
                "prompt": r.prompt,
                "kind": r.kind,
                "choices": r.choices,
                "default": r.default,
                "timeout_seconds": r.timeout_seconds,
            }
        }

    @app.post("/chat/{session_id}/cancel")
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

    @app.get("/chat/{session_id}/events")
    async def chat_events(session_id: str) -> StreamingResponse:
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

        Note: ``sessions`` is captured from the enclosing ``create_app``
        closure rather than injected via ``Depends``. FastAPI's
        dependency resolution on streaming endpoints interacts badly
        with some ASGI transports (the helyx port documented this as
        an ``httpx.ASGITransport`` hang) — direct closure capture is
        the same object at runtime and avoids the pitfall.
        """

        async def stream() -> AsyncIterator[bytes]:
            # Opening comment keeps intermediate proxies from buffering
            # the connection while we wait for the first real event.
            yield b": subscribed\n\n"
            async for event in sessions.subscribe(session_id):
                yield event.to_sse()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # nginx: don't buffer SSE
            },
        )

    @app.post(
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

    @app.get("/settings", response_model=SettingsPayload)
    async def get_settings() -> SettingsPayload:
        s = settings_store.get()
        return SettingsPayload(yolo_mode=s.yolo_mode)

    @app.post("/settings", response_model=SettingsPayload)
    async def update_settings(body: SettingsPayload) -> SettingsPayload:
        """Partial update: fields omitted in the body keep their
        current value. Returns the full post-update snapshot so the UI
        can reconcile with whatever the server actually accepted."""
        changes = {
            key: value
            for key, value in body.model_dump(exclude_unset=True).items()
            if value is not None
        }
        try:
            updated = settings_store.update(**changes)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        return SettingsPayload(yolo_mode=updated.yolo_mode)

    @app.get("/sessions")
    async def list_sessions(
        limit: int = 50,
        store: SessionStore = Depends(get_sessions),
    ) -> list[dict]:
        summaries = store.list(limit=limit)
        return [
            {
                "id": s.id,
                "title": s.title,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "message_count": s.message_count,
            }
            for s in summaries
        ]

    @app.get("/sessions/search")
    async def search_sessions(
        q: str = "",
        limit: int = 20,
        store: SessionStore = Depends(get_sessions),
    ) -> list[dict]:
        """Full-text search over session message content.

        Returns ``[]`` for a blank ``q``. Results are ordered by BM25
        relevance and include ``session_id``, ``title``, and a ``snippet``
        with matching terms wrapped in ``**``.
        """
        if not q.strip():
            return []
        return store.search(q.strip(), limit=max(1, min(limit, 100)))

    @app.get("/sessions/{session_id}")
    async def get_session(
        session_id: str,
        store: SessionStore = Depends(get_sessions),
    ) -> dict:
        session = store.get(session_id)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"session {session_id!r} not found")
        ts_list = getattr(session, "_message_timestamps", []) or []
        from datetime import datetime, timezone
        def _iso(ts: int | None) -> str | None:
            if ts is None:
                return None
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        return {
            "id": session.id,
            "title": session.title,
            "context": session.context,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "tool_calls": [tc.model_dump() for tc in m.tool_calls] if m.tool_calls else None,
                    "tool_call_id": m.tool_call_id,
                    "created_at": _iso(ts_list[i] if i < len(ts_list) else None),
                }
                for i, m in enumerate(session.history)
            ],
        }

    @app.patch("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def rename_session(
        session_id: str,
        body: dict,
        store: SessionStore = Depends(get_sessions),
    ) -> None:
        title = body.get("title")
        if title is not None:
            store.rename(session_id, title)

    @app.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_session(
        session_id: str,
        store: SessionStore = Depends(get_sessions),
    ) -> None:
        store.delete(session_id)

    @app.patch("/sessions/{session_id}/truncate", status_code=status.HTTP_204_NO_CONTENT)
    async def truncate_session(
        session_id: str,
        body: TruncateRequest,
        store: SessionStore = Depends(get_sessions),
    ) -> None:
        session = store.get(session_id)
        if session is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Session not found")
        truncated = session.history[: body.before_seq]
        store.replace_history(session_id, truncated)

    @app.get("/graph")
    async def get_agent_graph() -> dict:
        """Return the agent/skill/session graph for the UI graph view.
        Result is cached for 60 seconds to avoid rebuilding on every navigation."""
        from .graph import build_agent_graph
        return build_agent_graph(registry, sessions)

    @app.get("/graph/knowledge")
    async def get_knowledge_graph() -> dict:
        """Return the GraphRAG entity/relation graph for the Knowledge tab."""
        from ..agent.graphrag_manager import get_engine
        engine = get_engine()
        if engine is None:
            return {"nodes": [], "edges": [], "enabled": False}
        return engine.export_graph()

    @app.post("/graph/knowledge/query")
    async def knowledge_query(body: dict) -> dict:
        """Semantic search over the knowledge graph. Returns evidence + trace + subgraph."""
        query = body.get("query", "").strip()
        limit = min(int(body.get("limit", 10)), 50)
        if not query:
            return {"results": [], "trace": None, "subgraph": {"nodes": [], "edges": []}}
        from ..agent.graphrag_manager import get_engine
        engine = get_engine()
        if engine is None:
            return {"results": [], "trace": None, "subgraph": {"nodes": [], "edges": []}, "enabled": False}
        enriched = await engine.retrieve_enriched(query, top_k=limit)
        return {
            "enabled": True,
            "results": [
                {
                    "chunk_id": r.chunk_id,
                    "source_path": r.source_path,
                    "heading": r.heading,
                    "content": r.content,
                    "score": round(r.score, 4),
                    "source": r.source,
                    "related_entities": r.related_entities,
                }
                for r in enriched.results
            ],
            "trace": {
                "seed_entities": enriched.trace.seed_entities,
                "hops": [
                    {"from": h.from_entity, "to": h.to_entity, "relation": h.relation, "depth": h.hop_depth}
                    for h in enriched.trace.hops
                ],
                "expanded_entity_ids": enriched.trace.expanded_entity_ids,
            },
            "subgraph": {
                "nodes": enriched.subgraph_nodes,
                "edges": enriched.subgraph_edges,
            },
        }

    @app.get("/graph/knowledge/entities")
    async def knowledge_entities(
        type: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        from ..agent.graphrag_manager import get_engine
        engine = get_engine()
        if engine is None:
            return {"entities": [], "total": 0, "enabled": False}
        graph = engine._entity_graph
        entities = graph.list_entities(entity_type=type, search=search, limit=limit, offset=offset)
        total = graph.count_entities()
        return {
            "enabled": True,
            "entities": [
                {
                    "id": e.id, "name": e.name, "type": e.type,
                    "degree": graph.entity_degree(e.id),
                }
                for e in entities
            ],
            "total": total,
        }

    @app.get("/graph/knowledge/entity/{entity_id}")
    async def knowledge_entity_detail(entity_id: int) -> dict:
        from ..agent.graphrag_manager import get_engine
        engine = get_engine()
        if engine is None:
            return {"entity": None, "enabled": False}
        graph = engine._entity_graph
        entity = graph.get_entity(entity_id)
        if entity is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="entity not found")
        triples = graph.get_entity_triples(entity_id)
        chunk_ids = graph.chunks_for_entity(entity_id)
        chunks = []
        for cid in chunk_ids[:20]:
            cd = engine._get_chunk(cid)
            if cd:
                chunks.append({"chunk_id": cid, "source_path": cd.get("source_path", ""), "heading": cd.get("heading", "")})
        relations = []
        for t in triples:
            other_id = t.tail_id if t.head_id == entity_id else t.head_id
            other = graph.get_entity(other_id)
            direction = "outgoing" if t.head_id == entity_id else "incoming"
            relations.append({
                "entity_id": other_id,
                "entity_name": other.name if other else "?",
                "entity_type": other.type if other else "?",
                "relation": t.relation,
                "direction": direction,
                "strength": t.strength,
            })
        return {
            "enabled": True,
            "entity": {"id": entity.id, "name": entity.name, "type": entity.type, "description": entity.description},
            "degree": graph.entity_degree(entity_id),
            "relations": relations,
            "chunks": chunks,
        }

    @app.get("/graph/knowledge/subgraph")
    async def knowledge_subgraph(seed: int, hops: int = 2) -> dict:
        from ..agent.graphrag_manager import get_engine
        engine = get_engine()
        if engine is None:
            return {"nodes": [], "edges": [], "enabled": False}
        result = engine._entity_graph.subgraph(seed, max_hops=hops)
        return {
            "enabled": True,
            "nodes": [
                {"id": n.id, "name": n.name, "type": n.type, "degree": engine._entity_graph.entity_degree(n.id)}
                for n in result["nodes"]
            ],
            "edges": [
                {"source": e.head_id, "target": e.tail_id, "relation": e.relation, "strength": e.strength}
                for e in result["edges"]
            ],
        }

    @app.get("/graph/knowledge/stats")
    async def knowledge_stats() -> dict:
        from ..agent.graphrag_manager import get_engine
        engine = get_engine()
        if engine is None:
            return {"enabled": False, "entities": 0, "triples": 0, "types": {}, "components": []}
        graph = engine._entity_graph
        components = graph.connected_components()
        return {
            "enabled": True,
            "entities": graph.count_entities(),
            "triples": graph.count_triples(),
            "types": graph.entity_counts_by_type(),
            "components": [
                {"id": i, "size": len(c), "entities": c[:10]}
                for i, c in enumerate(components[:20])
            ],
            "component_count": len(components),
        }

    @app.get("/graph/knowledge/file-subgraph")
    async def knowledge_file_subgraph(path: str) -> dict:
        """Return entity subgraph for all entities extracted from a vault file."""
        from ..agent.graphrag_manager import source_subgraph
        return source_subgraph([path])

    @app.get("/graph/knowledge/folder-subgraph")
    async def knowledge_folder_subgraph(folder: str) -> dict:
        """Return entity subgraph for all entities from vault files in a folder."""
        from ..agent.graphrag_manager import source_subgraph
        from ..vault import list_tree
        prefix = folder if folder.endswith("/") else folder + "/"
        entries = list_tree()
        paths = [e.path for e in entries if e.type == "file" and e.path.startswith(prefix)]
        if not paths:
            return {"nodes": [], "edges": []}
        return source_subgraph(paths)

    @app.post("/graph/knowledge/index-file")
    async def graphrag_index_file(body: dict) -> dict:
        from ..agent.graphrag_manager import get_engine, index_vault_file
        from ..vault import read_file

        path = body.get("path", "").strip()
        if not path:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`path` required")
        if get_engine() is None:
            return {"enabled": False}
        try:
            result = read_file(path)
            content = result.get("content", "")
        except FileNotFoundError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file not found")
        if not content.strip():
            return {"queued": False, "reason": "empty file"}

        _graphrag_index_tasks[path] = {"status": "indexing"}

        async def _run() -> None:
            try:
                await index_vault_file(path, content)
                sg = source_subgraph([path])
                _graphrag_index_tasks[path] = {
                    "status": "done",
                    "node_count": len(sg.get("nodes", [])),
                    "edge_count": len(sg.get("edges", [])),
                }
            except Exception as exc:
                log.exception("graphrag index-file failed for %s", path)
                _graphrag_index_tasks[path] = {"status": "error", "detail": str(exc)}

        asyncio.get_running_loop().create_task(_run())
        return {"queued": True, "path": path}

    @app.get("/graph/knowledge/index-file-status")
    async def graphrag_index_file_status(path: str) -> dict:
        info = _graphrag_index_tasks.get(path)
        if info is None:
            return {"status": "unknown"}
        result = {**info}
        if info.get("status") == "done":
            from ..agent.graphrag_manager import source_subgraph
            sg = source_subgraph([path])
            result["nodes"] = sg.get("nodes", [])
            result["edges"] = sg.get("edges", [])
            _graphrag_index_tasks.pop(path, None)
        elif info.get("status") == "error":
            _graphrag_index_tasks.pop(path, None)
        return result

    @app.post("/graphrag/reindex")
    async def graphrag_reindex(full: bool = False) -> StreamingResponse:
        """Reindex vault into GraphRAG, streaming progress as SSE.

        Query param ``full=1`` drops all existing data first.
        Default (incremental) skips files whose content hasn't changed.
        """
        from ..agent.graphrag_manager import index_vault_streaming

        async def _gen():
            async for frame in index_vault_streaming(nexus_cfg, full=full):
                yield frame

        return StreamingResponse(
            _gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/insights")
    async def get_insights(
        days: int = 30,
        model: str | None = None,
        store: SessionStore = Depends(get_sessions),
    ) -> dict[str, Any]:
        """Return a usage analytics report for the last ``days`` days.

        Clamps ``days`` into ``[1, 365]``. Optional ``model`` scopes to sessions
        whose persisted model slug matches exactly.
        """
        from ..insights import InsightsEngine
        days = max(1, min(int(days), 365))
        engine = InsightsEngine(store._db_path)  # InsightsEngine reads loom's schema directly
        return engine.generate(days=days, model_filter=model or None)

    @app.get("/sessions/{session_id}/export")
    async def export_session(
        session_id: str,
        store: SessionStore = Depends(get_sessions),
    ) -> StreamingResponse:
        from datetime import datetime, timezone
        import re

        session = store.get(session_id)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"session {session_id!r} not found")

        # Gather session-level timestamps from the store.
        created_at_ts, updated_at_ts = store.get_session_timestamps(session_id)

        def _iso(ts: int) -> str:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

        # Build frontmatter (hand-rolled, no nested objects).
        context_val = session.context
        if context_val is None:
            context_yaml = "null"
        else:
            context_yaml = json.dumps(context_val)

        title_yaml = json.dumps(session.title)
        lines: list[str] = [
            "---",
            f"nexus_session_id: {session.id}",
            f"title: {title_yaml}",
            f"created_at: {_iso(created_at_ts)}",
            f"updated_at: {_iso(updated_at_ts)}",
            f"context: {context_yaml}",
            "---",
            "",
        ]

        ts_list: list[int] = getattr(session, "_message_timestamps", []) or []

        for i, msg in enumerate(session.history):
            role = str(msg.role.value if hasattr(msg.role, "value") else msg.role)
            # Skip tool/system messages and empty content.
            if role not in ("user", "assistant"):
                continue
            content = (msg.content or "").strip()
            if not content:
                continue
            msg_ts = ts_list[i] if i < len(ts_list) else created_at_ts
            label = "You" if role == "user" else "Nexus"
            lines.append(f"## {label} · {_iso(msg_ts)}")
            lines.append("")
            lines.append(content)
            lines.append("")

        markdown = "\n".join(lines)

        # Build a safe filename slug from the title.
        slug = re.sub(r"[^a-z0-9]+", "-", session.title.lower()).strip("-")[:40]
        id8 = session.id[:8]
        filename = f"session-{slug}-{id8}.md"

        return StreamingResponse(
            iter([markdown]),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    def _session_markdown(session: Any, include_frontmatter: bool = True) -> str:
        """Render a session as markdown. Shared between export and to-vault.
        Uses the `sessions` closure (not a per-request `store` name)."""
        from datetime import datetime, timezone
        created_at_ts, updated_at_ts = sessions.get_session_timestamps(session.id)

        def _iso(ts: int) -> str:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

        lines: list[str] = []
        if include_frontmatter:
            context_yaml = "null" if session.context is None else json.dumps(session.context)
            lines += [
                "---",
                f"nexus_session_id: {session.id}",
                f"title: {json.dumps(session.title)}",
                f"created_at: {_iso(created_at_ts)}",
                f"updated_at: {_iso(updated_at_ts)}",
                f"context: {context_yaml}",
                "---",
                "",
            ]

        ts_list: list[int] = getattr(session, "_message_timestamps", []) or []
        for i, msg in enumerate(session.history):
            role = str(msg.role.value if hasattr(msg.role, "value") else msg.role)
            if role not in ("user", "assistant"):
                continue
            content = (msg.content or "").strip()
            if not content:
                continue
            msg_ts = ts_list[i] if i < len(ts_list) else created_at_ts
            label = "You" if role == "user" else "Nexus"
            lines.append(f"## {label} · {_iso(msg_ts)}")
            lines.append("")
            lines.append(content)
            lines.append("")
        return "\n".join(lines)

    @app.post("/sessions/{session_id}/to-vault")
    async def session_to_vault(
        session_id: str,
        body: dict,
        store: SessionStore = Depends(get_sessions),
        a: Agent = Depends(get_agent),
    ) -> dict:
        """Save a session into the vault.

        Body: {"mode": "raw" | "summary", "path"?: str}

        - raw: dumps the session as markdown (same shape as the export endpoint)
          into `sessions/<slug>-<id8>.md` under the vault.
        - summary: calls the default LLM to produce a concise note and writes it
          to `notes/session-<slug>-<id8>.md` with YAML frontmatter tagging it.
        """
        import re
        from datetime import datetime, timezone
        from .. import vault as _vault
        from ..agent.llm import ChatMessage, Role

        mode = (body.get("mode") or "raw").lower()
        explicit_path: str | None = body.get("path")
        session = store.get(session_id)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"session {session_id!r} not found")

        slug = re.sub(r"[^a-z0-9]+", "-", (session.title or "session").lower()).strip("-")[:40] or "session"
        id8 = session.id[:8]

        if mode == "raw":
            md = _session_markdown(session, include_frontmatter=True)
            path = explicit_path or f"sessions/{slug}-{id8}.md"
            try:
                _vault.write_file(path, md)
            except (ValueError, OSError) as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
            return {"mode": "raw", "path": path, "bytes": len(md.encode("utf-8"))}

        if mode == "summary":
            # Render the conversation (no frontmatter — just the exchange).
            convo = _session_markdown(session, include_frontmatter=False)
            if not convo.strip():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="session has no user/assistant turns")
            sys_prompt = (
                "You are summarizing a chat session for a personal knowledge base. Output ONLY markdown — "
                "no preamble like 'Here is the summary'. Keep it compact (200–500 words). Structure:\n"
                "## Goal\n(1–2 sentences)\n"
                "## Key decisions\n(bullets)\n"
                "## Actions / next steps\n(bullets, imperative)\n"
                "## References\n(file paths, URLs, commands mentioned — verbatim)\n"
                "Be specific, skip pleasantries, preserve exact names and numbers."
            )
            user_prompt = f"Session title: {session.title}\n\n{convo}"
            # Route through the same provider resolver the agent loop uses so
            # the upstream model name is passed correctly (OpenAI-compat and
            # Anthropic both require it). Falls back to the env-var provider
            # if no registry is wired.
            cfg = _state.get("cfg")
            default_model = cfg.agent.default_model if cfg and cfg.agent else None
            try:
                provider, upstream = a._resolve_provider(default_model)
                resp = await provider.chat(
                    messages=[
                        ChatMessage(role=Role.SYSTEM, content=sys_prompt),
                        ChatMessage(role=Role.USER, content=user_prompt),
                    ],
                    tools=[],
                    model=upstream,
                )
                summary = (resp.content or "").strip()
            except Exception as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"summary failed: {exc}")
            if not summary:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="empty summary from model")
            now_iso = datetime.now(tz=timezone.utc).isoformat()
            fm = (
                "---\n"
                f"source: chat-session\n"
                f"session_id: {session.id}\n"
                f"title: {json.dumps(session.title)}\n"
                f"summarized_at: {now_iso}\n"
                f"tags: [session-summary]\n"
                "---\n\n"
            )
            note = fm + summary + ("\n" if not summary.endswith("\n") else "")
            path = explicit_path or f"notes/session-{slug}-{id8}.md"
            try:
                _vault.write_file(path, note)
            except (ValueError, OSError) as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
            return {"mode": "summary", "path": path, "length": len(summary)}

        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"unknown mode: {mode!r}")

    @app.post("/sessions/import")
    async def import_session(
        request: "Request",
        store: SessionStore = Depends(get_sessions),
    ) -> dict:
        from datetime import datetime, timezone
        import re
        import uuid as _uuid
        from ..agent.llm import ChatMessage, Role

        content_type = request.headers.get("content-type", "")

        if "multipart/form-data" in content_type:
            form = await request.form()
            file_field = form.get("file")
            if file_field is None:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`file` field required")
            markdown = (await file_field.read()).decode("utf-8")  # type: ignore[union-attr]
        else:
            body = await request.json()
            markdown = body.get("markdown", "")
            if not markdown:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`markdown` required")

        # Parse optional YAML frontmatter.
        fm: dict[str, str] = {}
        body_text = markdown
        fm_match = re.match(r"^---\n(.*?)\n---\n?", markdown, re.DOTALL)
        if fm_match:
            for line in fm_match.group(1).splitlines():
                if ": " in line:
                    k, _, v = line.partition(": ")
                    fm[k.strip()] = v.strip()
            body_text = markdown[fm_match.end():]

        # Determine title.
        title = "Imported session"
        if "title" in fm:
            try:
                title = json.loads(fm["title"])
            except Exception:
                title = fm["title"].strip('"\'')
        else:
            h1 = re.search(r"^#\s+(.+)$", body_text, re.MULTILINE)
            if h1:
                title = h1.group(1).strip()

        context: str | None = None
        if "context" in fm:
            raw_ctx = fm["context"].strip()
            if raw_ctx and raw_ctx.lower() != "null":
                try:
                    context = json.loads(raw_ctx)
                except Exception:
                    context = raw_ctx

        # Assign id — avoid clobbering existing sessions.
        new_id = _uuid.uuid4().hex
        if "nexus_session_id" in fm:
            candidate = fm["nexus_session_id"].strip()
            if candidate and store.get(candidate) is None:
                new_id = candidate

        # Reconstruct messages from level-2 headings.
        messages: list[ChatMessage] = []
        now = int(datetime.now(tz=timezone.utc).timestamp())
        sections = re.split(r"\n## (You|Nexus) · [^\n]*\n", body_text)
        # sections[0] is text before first heading (ignored); then alternating label/content pairs.
        i = 1
        while i + 1 < len(sections):
            speaker = sections[i].strip()
            content = sections[i + 1].strip()
            i += 2
            if not content:
                continue
            role = Role.USER if speaker == "You" else Role.ASSISTANT
            messages.append(ChatMessage(role=role, content=content))

        # Insert into store via the public import_session method.
        store.import_session(new_id, title, context, messages, now)

        return {"id": new_id, "title": title, "imported_message_count": len(messages)}

    # ── vault routes ───────────────────────────────────────────────────────────

    @app.get("/vault/tree")
    async def vault_tree() -> list[dict]:
        from ..vault import list_tree
        entries = list_tree()
        return [{"path": e.path, "type": e.type, "size": e.size, "mtime": e.mtime} for e in entries]

    @app.get("/vault/tags")
    async def vault_list_tags() -> list[dict]:
        from .. import vault_index
        if vault_index.is_empty():
            vault_index.rebuild_from_disk()
        return vault_index.list_tags()

    @app.get("/vault/tags/{tag}")
    async def vault_files_for_tag(tag: str) -> dict:
        from .. import vault_index
        if vault_index.is_empty():
            vault_index.rebuild_from_disk()
        return {"tag": tag, "files": vault_index.files_with_tag(tag)}

    @app.get("/vault/backlinks")
    async def vault_backlinks_endpoint(path: str) -> dict:
        from .. import vault_index
        if vault_index.is_empty():
            vault_index.rebuild_from_disk()
        return {"path": path, "backlinks": vault_index.backlinks(path)}

    @app.get("/vault/forward-links")
    async def vault_forward_links_endpoint(path: str) -> dict:
        from .. import vault_index
        if vault_index.is_empty():
            vault_index.rebuild_from_disk()
        return {"path": path, "forward_links": vault_index.forward_links(path)}

    @app.get("/vault/raw")
    async def vault_read_raw(path: str):
        """Stream raw file bytes from the vault with a guessed Content-Type.

        Used by the UI to render images, PDFs, video, audio, and to provide
        a direct "open in new tab" link for any vault file.
        """
        import mimetypes
        from fastapi.responses import FileResponse
        from ..vault import resolve_path
        try:
            full = resolve_path(path)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        if not full.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no such file: {path!r}")
        mime, _ = mimetypes.guess_type(full.name)
        return FileResponse(
            full,
            media_type=mime or "application/octet-stream",
            filename=full.name,
        )

    @app.get("/vault/file")
    async def vault_read_file(path: str) -> dict:
        from ..vault import read_file
        from .. import vault_index
        try:
            result = read_file(path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        try:
            if vault_index.is_empty():
                vault_index.rebuild_from_disk()
            result["tags"] = vault_index.tags_for_file(path)
            result["backlinks"] = vault_index.backlinks(path)
        except Exception:
            log.warning("vault_index: failed to attach tags/backlinks", exc_info=True)
        return result

    @app.put("/vault/file", status_code=status.HTTP_204_NO_CONTENT)
    async def vault_write_file(body: dict) -> None:
        from ..vault import write_file
        path = body.get("path", "")
        content = body.get("content", "")
        if not path:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`path` required")
        try:
            write_file(path, content)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    @app.delete("/vault/file", status_code=status.HTTP_204_NO_CONTENT)
    async def vault_delete_file(path: str, recursive: bool = False) -> None:
        from ..vault import delete
        try:
            delete(path, recursive=recursive)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    @app.post("/vault/folder", status_code=status.HTTP_201_CREATED)
    async def vault_create_folder(body: dict) -> dict:
        from ..vault import create_folder
        path = body.get("path", "")
        if not path:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`path` required")
        try:
            create_folder(path)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        return {"path": path}

    @app.post("/vault/upload")
    async def vault_upload(request: "Request") -> dict:
        from ..vault import write_file, write_file_bytes

        content_type = request.headers.get("content-type", "")
        if "multipart/form-data" not in content_type:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="expected multipart/form-data",
            )
        form = await request.form()
        files = form.getlist("files")
        if not files:
            file_field = form.get("file")
            if file_field is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="`file` or `files` field required",
                )
            files = [file_field]
        dest_dir = (form.get("path") or "").strip().strip("/")
        uploaded: list[dict[str, Any]] = []
        for upload in files:
            if not hasattr(upload, "filename") or upload.filename is None:
                continue
            import re as _re

            safe_name = _re.sub(r"[^\w.\-]+", "_", upload.filename)
            rel = f"{dest_dir}/{safe_name}" if dest_dir else safe_name
            raw = await upload.read()
            text_exts = {
                ".md", ".mdx", ".txt", ".markdown", ".csv", ".json",
                ".yaml", ".yml", ".toml", ".xml", ".html", ".css",
                ".js", ".ts", ".py", ".rs", ".go", ".sh", ".bash", ".zsh",
            }
            _, ext = os.path.splitext(safe_name.lower())
            try:
                if ext in text_exts:
                    write_file(rel, raw.decode("utf-8", errors="replace"))
                else:
                    write_file_bytes(rel, raw)
            except (ValueError, OSError) as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
            uploaded.append({"path": rel, "size": len(raw)})
        return {"uploaded": uploaded}

    @app.get("/vault/search")
    async def vault_search_endpoint(q: str = "", limit: int = 50) -> dict:
        from .. import vault_search
        q = q.strip()
        if not q:
            return {"results": [], "q": q, "count": 0}
        if vault_search.is_empty():
            vault_search.rebuild_from_disk()
        results = vault_search.search(q, limit=limit)
        return {"results": results, "q": q, "count": len(results)}

    @app.post("/vault/reindex")
    async def vault_reindex() -> dict:
        from .. import vault_search
        n = vault_search.rebuild_from_disk()
        return {"indexed": n}

    @app.get("/vault/graph")
    async def vault_graph(
        scope: str = "all",
        seed: str = "",
        hops: int = 1,
        edge_types: str = "link",
    ) -> dict:
        from ..vault_graph import build_graph, build_scoped_graph
        if scope == "all" and not seed:
            data = build_graph()
            return {
                "nodes": data["nodes"],
                "edges": [{"from": e["from_"], "to": e["to"]} for e in data["edges"]],
                "orphans": data["orphans"],
            }
        hops = max(1, min(int(hops), 3))
        data = build_scoped_graph(scope=scope, seed=seed, hops=hops, edge_types=edge_types)
        return {
            "nodes": data["nodes"],
            "edges": [{"from": e["from_"], "to": e["to_"], "type": e["type"]} for e in data["edges"]],
            "entity_nodes": data["entity_nodes"],
            "orphans": data["orphans"],
        }

    @app.get("/vault/graph/entity-sources")
    async def vault_graph_entity_sources(path: str) -> dict:
        from ..agent.graphrag_manager import entities_for_source
        return {"path": path, "entities": entities_for_source(path)}

    @app.get("/vault/graph/source-files")
    async def vault_graph_source_files(entity_id: int) -> dict:
        from ..agent.graphrag_manager import sources_for_entity
        return {"entity_id": entity_id, "source_files": sources_for_entity(entity_id)}

    @app.post("/vault/move", status_code=status.HTTP_204_NO_CONTENT)
    async def vault_move(body: dict) -> None:
        from ..vault import move
        from_path = body.get("from", "")
        to_path = body.get("to", "")
        if not from_path or not to_path:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`from` and `to` required")
        try:
            move(from_path, to_path)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    # ── vault kanban routes ────────────────────────────────────────────────────
    # Kanban lives inside the vault as a plain .md file with
    # `kanban-plugin: basic` frontmatter (Obsidian-compatible).

    @app.get("/vault/kanban")
    async def vault_kanban_get(path: str) -> dict:
        from .. import vault_kanban
        try:
            board = vault_kanban.read_board(path)
        except FileNotFoundError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file not found")
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        return {"path": path, **board.to_dict()}

    @app.post("/vault/kanban", status_code=status.HTTP_201_CREATED)
    async def vault_kanban_create(body: dict) -> dict:
        """Scaffold a new kanban .md file. Body: {path, title?, columns?}."""
        from .. import vault_kanban
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

    @app.patch("/vault/kanban/cards/{card_id}")
    async def vault_kanban_patch_card(card_id: str, body: dict, path: str) -> dict:
        """Update title/body or move between lanes. Body: {title?, body?, lane?, position?}."""
        from .. import vault_kanban
        try:
            if "lane" in body:
                card = vault_kanban.move_card(
                    path, card_id, body["lane"], body.get("position"),
                )
                # Also apply any content edits in the same call.
                updates = {k: body[k] for k in ("title", "body", "session_id", "status") if k in body}
                if updates:
                    card = vault_kanban.update_card(path, card_id, updates)
            else:
                updates = {k: body[k] for k in ("title", "body", "session_id", "status") if k in body}
                card = vault_kanban.update_card(path, card_id, updates)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        return card.to_dict()

    @app.post("/vault/kanban/cards", status_code=status.HTTP_201_CREATED)
    async def vault_kanban_add_card(body: dict, path: str) -> dict:
        from .. import vault_kanban
        lane = body.get("lane", "")
        title = body.get("title", "")
        if not lane or not title:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`lane` and `title` required")
        try:
            card = vault_kanban.add_card(path, lane, title, body.get("body", ""))
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        return card.to_dict()

    @app.delete("/vault/kanban/cards/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def vault_kanban_delete_card(card_id: str, path: str) -> None:
        from .. import vault_kanban
        try:
            vault_kanban.delete_card(path, card_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    @app.post("/vault/kanban/lanes", status_code=status.HTTP_201_CREATED)
    async def vault_kanban_add_lane(body: dict, path: str) -> dict:
        from .. import vault_kanban
        title = body.get("title", "")
        if not title:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`title` required")
        lane = vault_kanban.add_lane(path, title)
        return lane.to_dict()

    @app.patch("/vault/kanban/lanes/{lane_id}")
    async def vault_kanban_patch_lane(lane_id: str, body: dict, path: str) -> dict:
        """Update a lane's title or prompt. Body: {title?, prompt?}."""
        from .. import vault_kanban
        try:
            lane = vault_kanban.update_lane(path, lane_id, body)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        return lane.to_dict()

    @app.delete("/vault/kanban/lanes/{lane_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def vault_kanban_delete_lane(lane_id: str, path: str) -> None:
        from .. import vault_kanban
        vault_kanban.delete_lane(path, lane_id)

    # ── vault dispatch ─────────────────────────────────────────────────────────
    # Start a new chat session seeded with the content of a vault file
    # (or a single kanban card). Returns the new session_id + a seed message
    # the client can pre-fill in the input.

    # Marker that the UI strips from displayed messages. The agent still
    # sees the full content (it's persisted like any user message), but
    # the chat bubble list filters messages starting with this sentinel.
    HIDDEN_SEED_MARKER = "<!-- nx:hidden-seed -->\n"

    def _compose_card_context_seed(
        *, lane_prompt: str | None, path: str, card_title: str, card_id: str, card_body: str,
    ) -> str:
        folder = path.rsplit("/", 1)[0] if "/" in path else ""
        parts = []
        if lane_prompt:
            parts.append(lane_prompt.strip())
            parts.append("")
        parts.append(f"**Board:** `{path}`")
        if folder:
            parts.append(f"**Folder:** `{folder}/`")
        parts.append(f"**Card:** {card_title}")
        parts.append(f"**Card ID:** `{card_id}`")
        parts.append("")
        if card_body.strip():
            parts.append(card_body.strip())
            parts.append("")
        parts.append(
            "*Context hints: related files typically live in the same folder as the board. "
            "To add tasks or sub-plans, edit the board file — cards are `### Heading` blocks "
            "under a `## Lane` heading with `<!-- nx:id=... -->` metadata. Use your vault tools.*"
        )
        return "\n".join(parts)

    def _compose_hidden_chat_seed(*, path: str, card_title: str, card_id: str, card_body: str) -> str:
        folder = path.rsplit("/", 1)[0] if "/" in path else ""
        body = [
            HIDDEN_SEED_MARKER.rstrip(),
            f"The user just opened this kanban card. Check the board file at `{path}`, "
            f"read the card, suggest 2-3 concrete next steps to the user, and then wait for instructions. "
            f"Don't make changes yet.",
            "",
            f"**Board:** `{path}`",
        ]
        if folder:
            body.append(f"**Folder:** `{folder}/`")
        body.append(f"**Card:** {card_title}")
        body.append(f"**Card ID:** `{card_id}`")
        if card_body.strip():
            body.append("")
            body.append(card_body.strip())
        return "\n".join(body)

    async def _run_background_agent_turn(
        *,
        session_id: str,
        seed_message: str,
        card_path: str,
        card_id: str,
        agent_: Agent,
        store: SessionStore,
    ) -> None:
        """Run one agent turn to completion, publishing events via the trace bus
        and updating the card's status (done/failed) when finished."""
        from .. import vault_kanban
        token = CURRENT_SESSION_ID.set(session_id)
        try:
            session = store.get_or_create(session_id)
            pre_turn = list(session.history)
            final_messages = None
            accumulated_text = ""
            accumulated_tools: list[dict[str, Any]] = []
            try:
                from ..agent.llm import ChatMessage as _CM, Role as _R
                store.replace_history(
                    session_id, pre_turn + [_CM(role=_R.USER, content=seed_message)],
                )
            except Exception:
                log.exception("background dispatch: pre-turn persist failed")
            try:
                async for event in agent_.run_turn_stream(
                    seed_message,
                    history=session.history,
                    context=session.context,
                    session_id=session_id,
                ):
                    etype = event.get("type")
                    if etype == "delta":
                        accumulated_text += event.get("text", "")
                    elif etype in ("tool_exec_start", "tool_exec_result"):
                        if etype == "tool_exec_start":
                            accumulated_tools.append({
                                "name": event.get("name", ""),
                                "args": event.get("args"),
                                "status": "pending",
                            })
                        else:
                            for t in reversed(accumulated_tools):
                                if t.get("name") == event.get("name") and t.get("status") == "pending":
                                    t["status"] = "done"
                                    t["result_preview"] = event.get("result_preview")
                                    break
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
                        except Exception:
                            log.exception("background dispatch: bump_usage failed")
                new_status = "done" if final_messages is not None else "failed"
            except Exception:
                log.exception("background dispatch: agent loop crashed")
                new_status = "failed"
            finally:
                if final_messages is not None:
                    try:
                        store.replace_history(session_id, final_messages)
                    except Exception:
                        log.exception("background dispatch: final persist failed")
                else:
                    try:
                        store.persist_partial_turn(
                            session_id,
                            base_history=pre_turn,
                            user_message=seed_message,
                            assistant_text=accumulated_text,
                            tool_calls=accumulated_tools,
                            status_note="background_interrupted",
                        )
                    except Exception:
                        log.exception("background dispatch: partial persist failed")
            try:
                vault_kanban.update_card(card_path, card_id, {"status": new_status})
            except Exception:
                log.exception("background dispatch: card status update failed")
        finally:
            CURRENT_SESSION_ID.reset(token)

    @app.post("/vault/dispatch", status_code=status.HTTP_201_CREATED)
    async def vault_dispatch(
        body: dict,
        a: Agent = Depends(get_agent),
        store: SessionStore = Depends(get_sessions),
    ) -> dict:
        """Create a chat session seeded from a vault file or kanban card.

        Body: ``{path, card_id?, mode?}`` where ``mode`` is one of:
          - ``"chat"`` (default): returns a seed the UI prefills into its input.
          - ``"background"``: starts the agent server-side; UI doesn't navigate.
            Stamps ``status=running`` on the card and updates to ``done``/``failed``
            when the turn finishes. Requires ``card_id``.
          - ``"chat-hidden"``: creates a session, seeds with a hidden user message,
            kicks off no background work — the UI will POST to ``/chat/stream`` itself
            with the returned ``seed_message``, which embeds a marker the chat view
            filters out of the displayed message list.
        """
        from .. import vault, vault_kanban
        path = body.get("path", "")
        card_id = body.get("card_id")
        mode = body.get("mode") or "chat"
        if mode not in ("chat", "background", "chat-hidden"):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid mode")
        if not path:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`path` required")
        try:
            file = vault.read_file(path)
        except FileNotFoundError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file not found")

        title = path.rsplit("/", 1)[-1]
        seed_body = file.get("body") or file["content"]
        seed_title = title
        lane_prompt: str | None = None

        if card_id:
            try:
                board = vault_kanban.parse(file["content"])
            except Exception as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
            found = vault_kanban._find_card(board, card_id)
            if found is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="card not found")
            lane, card, _ = found
            seed_title = card.title
            seed_body = card.body
            lane_prompt = lane.prompt

        if mode == "background" and not card_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="background dispatch requires a card_id",
            )

        context_str = f"Dispatched from vault file: {path}"
        if card_id:
            context_str += f" (card {card_id})"
        session = store.create(context=context_str)

        # Title the session with the card title (or filename) up-front so the
        # sidebar doesn't show "New session" while the agent thinks.
        try:
            store.rename(session.id, (seed_title or title).strip()[:60])
        except Exception:
            log.exception("dispatch: title rename failed")

        if mode == "chat":
            seed_message = (
                f"# {seed_title}\n\n{seed_body}".strip()
                if seed_body
                else f"# {seed_title}"
            )
        elif mode == "chat-hidden":
            seed_message = _compose_hidden_chat_seed(
                path=path, card_title=seed_title, card_id=card_id or "", card_body=seed_body or "",
            )
        else:  # background
            seed_message = _compose_card_context_seed(
                lane_prompt=lane_prompt, path=path, card_title=seed_title,
                card_id=card_id or "", card_body=seed_body or "",
            )

        if card_id:
            updates: dict[str, Any] = {"session_id": session.id}
            if mode == "background":
                updates["status"] = "running"
            try:
                vault_kanban.update_card(path, card_id, updates)
            except Exception:
                import logging
                logging.getLogger(__name__).warning(
                    "dispatch: could not link session to card", exc_info=True,
                )

        if mode == "background":
            asyncio.create_task(
                _run_background_agent_turn(
                    session_id=session.id,
                    seed_message=seed_message,
                    card_path=path,
                    card_id=card_id,
                    agent_=a,
                    store=store,
                )
            )
            # Don't leak the seed to the client on background — caller doesn't need it.
            return {"session_id": session.id, "path": path, "card_id": card_id, "mode": mode}

        return {
            "session_id": session.id,
            "seed_message": seed_message,
            "path": path,
            "card_id": card_id,
            "mode": mode,
        }

    # ── config routes ──────────────────────────────────────────────────────────

    def _redact_cfg(cfg: Any) -> dict[str, Any]:
        if cfg is None:
            return {}
        import os
        from ..secrets import get as secrets_get
        out: dict[str, Any] = {
            "agent": cfg.agent.model_dump(),
            "providers": {},
            "models": [m.model_dump() for m in cfg.models],
        }
        for name, p in cfg.providers.items():
            key_source: str | None = None
            if p.type == "ollama":
                key_source = "anonymous"
            elif p.use_inline_key and secrets_get(name):
                key_source = "inline"
            elif p.api_key_env and os.environ.get(p.api_key_env):
                key_source = "env"
            has_key = key_source is not None
            out["providers"][name] = {
                "base_url": p.base_url,
                "key_env": p.api_key_env,
                "has_key": has_key,
                "use_inline_key": p.use_inline_key,
                "type": p.type,
            }
        t = cfg.transcription
        out["transcription"] = {
            "mode": t.mode,
            "model": t.model,
            "language": t.language or "",
            "device": t.device,
            "compute_type": t.compute_type,
            "remote": {
                "base_url": t.remote.base_url,
                "api_key_env": t.remote.api_key_env,
                "model": t.remote.model,
            },
        }
        return out

    def _rebuild_registry(cfg: Any) -> None:
        from ..agent.registry import build_registry
        new_reg = build_registry(cfg)
        _state["prov_reg"] = new_reg
        agent._provider_registry = new_reg
        agent._nexus_cfg = cfg
        _state["cfg"] = cfg

    @app.get("/config")
    async def get_config() -> dict[str, Any]:
        return _redact_cfg(_state["cfg"])

    @app.patch("/config")
    async def patch_config(body: dict[str, Any]) -> dict[str, Any]:
        from ..config_file import load as load_cfg, save as save_cfg, NexusConfig
        cfg = _state["cfg"] or load_cfg()
        raw = cfg.model_dump()
        # Shallow merge for "agent"; NESTED merge for "providers" so a partial
        # edit (e.g. base_url only) doesn't wipe fields like `type` that the
        # client didn't send. "has_key" is a read-only synthesized flag and is
        # never persisted.
        if "agent" in body:
            raw["agent"].update(body["agent"])
        if "providers" in body:
            for pname, patch in body["providers"].items():
                existing = raw["providers"].get(pname, {})
                merged = {**existing, **{k: v for k, v in patch.items() if k != "has_key"}}
                raw["providers"][pname] = merged
        if "models" in body:
            raw["models"] = body["models"]
        if "transcription" in body:
            existing = raw.get("transcription", {}) or {}
            patch = body["transcription"] or {}
            merged = {**existing, **{k: v for k, v in patch.items() if k != "remote"}}
            if "remote" in patch:
                merged["remote"] = {
                    **(existing.get("remote") or {}),
                    **(patch["remote"] or {}),
                }
            if isinstance(merged.get("language"), str) and not merged["language"].strip():
                merged["language"] = None
            raw["transcription"] = merged
        new_cfg = NexusConfig(**raw)
        save_cfg(new_cfg)
        _rebuild_registry(new_cfg)
        return _redact_cfg(new_cfg)

    @app.get("/providers")
    async def list_providers() -> list[dict[str, Any]]:
        import os
        from ..secrets import get as secrets_get
        cfg = _state["cfg"]
        if not cfg:
            return []
        result = []
        for name, p in cfg.providers.items():
            key_source: str | None = None
            if p.type == "ollama":
                key_source = "anonymous"
            elif p.use_inline_key and secrets_get(name):
                key_source = "inline"
            elif p.api_key_env and os.environ.get(p.api_key_env):
                key_source = "env"
            result.append({
                "name": name,
                "base_url": p.base_url,
                "has_key": key_source is not None,
                "key_source": key_source,
                "key_env": p.api_key_env,
                "type": p.type,
            })
        return result

    @app.get("/providers/{name}/models")
    async def list_provider_models(name: str) -> dict[str, Any]:
        import os
        import httpx as _httpx
        from ..secrets import get as secrets_get

        cfg = _state["cfg"]
        if not cfg or name not in cfg.providers:
            return {"models": [], "ok": False, "error": f"provider {name!r} not found"}

        p = cfg.providers[name]
        provider_type = p.type or ("anthropic" if name == "anthropic" else "openai_compat")

        try:
            async with _httpx.AsyncClient(timeout=5.0) as client:
                if provider_type == "ollama":
                    base = (p.base_url or "http://localhost:11434").rstrip("/")
                    # Try /api/tags first (native Ollama endpoint)
                    try:
                        r = await client.get(f"{base}/api/tags")
                        if r.status_code == 200:
                            data = r.json()
                            models = [m["name"] for m in data.get("models", [])]
                            return {"models": models, "ok": True, "error": None}
                        elif r.status_code == 404:
                            # Fall back to OpenAI-compat /v1/models
                            r2 = await client.get(f"{base}/v1/models")
                            if r2.status_code == 200:
                                data2 = r2.json()
                                models = [m["id"] for m in data2.get("data", [])]
                                return {"models": models, "ok": True, "error": None}
                            else:
                                return {"models": [], "ok": False, "error": f"HTTP {r2.status_code} from {base}/v1/models"}
                        else:
                            return {"models": [], "ok": False, "error": f"HTTP {r.status_code} from {base}/api/tags"}
                    except _httpx.ConnectError as exc:
                        return {"models": [], "ok": False, "error": f"connection refused — is Ollama running? ({exc})"}

                elif provider_type == "anthropic":
                    # Resolve key
                    api_key = ""
                    if p.use_inline_key:
                        api_key = secrets_get(name) or ""
                    if not api_key and p.api_key_env:
                        api_key = os.environ.get(p.api_key_env, "")
                    if not api_key:
                        return {"models": [], "ok": False, "error": "no API key configured for anthropic — set ANTHROPIC_API_KEY or use nexus providers set-key"}
                    r = await client.get(
                        "https://api.anthropic.com/v1/models",
                        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                    )
                    if r.status_code != 200:
                        return {"models": [], "ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
                    data = r.json()
                    models = [m["id"] for m in data.get("data", [])]
                    return {"models": models, "ok": True, "error": None}

                else:
                    # openai_compat
                    if not p.base_url:
                        return {"models": [], "ok": False, "error": "base_url not configured for this provider"}
                    api_key = ""
                    if p.use_inline_key:
                        api_key = secrets_get(name) or ""
                    if not api_key and p.api_key_env:
                        api_key = os.environ.get(p.api_key_env, "")
                    if not api_key:
                        return {"models": [], "ok": False, "error": f"no API key configured — set {p.api_key_env or 'an API key'} or use nexus providers set-key"}
                    headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"}
                    base = p.base_url.rstrip("/")
                    r = await client.get(f"{base}/models", headers=headers)
                    if r.status_code != 200:
                        return {"models": [], "ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
                    data = r.json()
                    models = [m["id"] for m in data.get("data", [])]
                    return {"models": models, "ok": True, "error": None}

        except _httpx.TimeoutException:
            return {"models": [], "ok": False, "error": "request timed out (5s)"}
        except Exception as exc:
            return {"models": [], "ok": False, "error": str(exc)}

    @app.post("/providers/{name}/key", status_code=status.HTTP_204_NO_CONTENT)
    async def set_provider_key(name: str, body: dict[str, Any]) -> None:
        from ..config_file import load as load_cfg, save as save_cfg
        from .. import secrets as _secrets
        api_key = body.get("api_key", "")
        if not api_key:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="api_key required")
        cfg = _state["cfg"] or load_cfg()
        if name not in cfg.providers:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"provider {name!r} not found")
        _secrets.set(name, api_key)
        cfg.providers[name].use_inline_key = True
        save_cfg(cfg)
        _rebuild_registry(cfg)

    @app.delete("/providers/{name}/key", status_code=status.HTTP_204_NO_CONTENT)
    async def clear_provider_key(name: str) -> None:
        from ..config_file import load as load_cfg, save as save_cfg
        from .. import secrets as _secrets
        cfg = _state["cfg"] or load_cfg()
        if name not in cfg.providers:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"provider {name!r} not found")
        _secrets.delete(name)
        cfg.providers[name].use_inline_key = False
        save_cfg(cfg)
        _rebuild_registry(cfg)

    @app.get("/models")
    async def list_models() -> list[dict[str, Any]]:
        cfg = _state["cfg"]
        if not cfg:
            return []
        return [m.model_dump() for m in cfg.models]

    @app.post("/models", status_code=status.HTTP_201_CREATED)
    async def add_model(body: dict[str, Any]) -> dict[str, Any]:
        from ..config_file import load as load_cfg, save as save_cfg, ModelEntry
        from ..agent.model_profiles import suggest_tier
        cfg = _state["cfg"] or load_cfg()
        # Legacy callers may still send `strengths` — drop it silently.
        body.pop("strengths", None)
        if not body.get("tier"):
            body["tier"] = suggest_tier(body.get("model_name", ""))
        m = ModelEntry(**body)
        cfg.models.append(m)
        # Auto-set as default if nothing is set yet — the DWIM path: a user
        # who just configured their first model expects it to be usable.
        if not cfg.agent.default_model:
            cfg.agent.default_model = m.id
        save_cfg(cfg)
        _rebuild_registry(cfg)
        return m.model_dump()

    @app.patch("/models/{model_id:path}")
    async def patch_model(model_id: str, body: dict[str, Any]) -> dict[str, Any]:
        from ..config_file import load as load_cfg, save as save_cfg
        from fastapi import HTTPException
        cfg = _state["cfg"] or load_cfg()
        for i, m in enumerate(cfg.models):
            if m.id == model_id:
                # id and provider are immutable after creation — avoid cascading
                # breakage (role assignments, last_used_model references).
                updates = {k: v for k, v in body.items() if k in {"model_name", "tags", "tier", "notes"}}
                if "tier" in updates and updates["tier"] not in ("fast", "balanced", "heavy"):
                    raise HTTPException(400, "tier must be fast|balanced|heavy")
                cfg.models[i] = m.model_copy(update=updates)
                save_cfg(cfg)
                _rebuild_registry(cfg)
                return cfg.models[i].model_dump()
        raise HTTPException(404, f"model {model_id!r} not found")

    @app.post("/models/suggest-tier")
    async def suggest_tier_endpoint(body: dict[str, Any]) -> dict[str, str]:
        from ..agent.model_profiles import suggest_tier, suggestion_source
        name = body.get("model_name", "") or ""
        return {"tier": suggest_tier(name), "source": suggestion_source(name)}

    @app.delete("/models/{model_id:path}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_model(model_id: str) -> None:
        from ..config_file import load as load_cfg, save as save_cfg
        cfg = _state["cfg"] or load_cfg()
        cfg.models = [m for m in cfg.models if m.id != model_id]
        save_cfg(cfg)
        _rebuild_registry(cfg)

    @app.get("/routing")
    async def get_routing() -> dict[str, Any]:
        cfg = _state["cfg"]
        pr = _state["prov_reg"]
        available = pr.available_model_ids() if pr else []
        if not cfg:
            return {"default_model": None, "last_used_model": None,
                    "classification_model": None, "available_models": available}
        embedding_id = cfg.graphrag.embedding_model_id
        chat_available = [m for m in available if m != embedding_id]
        return {
            "default_model": cfg.agent.default_model,
            "last_used_model": cfg.agent.last_used_model,
            "classification_model": cfg.agent.classification_model,
            "routing_mode": cfg.agent.routing_mode,
            "available_models": chat_available,
            "embedding_model_id": embedding_id,
            "extraction_model_id": cfg.graphrag.extraction_model_id,
        }

    @app.put("/routing")
    async def set_routing(body: dict[str, Any]) -> dict[str, Any]:
        from ..config_file import load as load_cfg, save as save_cfg
        cfg = _state["cfg"] or load_cfg()
        if "default_model" in body:
            cfg.agent.default_model = body["default_model"]
        if "last_used_model" in body:
            cfg.agent.last_used_model = body["last_used_model"]
        if "classification_model" in body:
            cfg.agent.classification_model = body["classification_model"]
        if "routing_mode" in body and body["routing_mode"] in ("fixed", "auto"):
            cfg.agent.routing_mode = body["routing_mode"]
        if "embedding_model_id" in body:
            cfg.graphrag.embedding_model_id = body["embedding_model_id"]
        if "extraction_model_id" in body:
            cfg.graphrag.extraction_model_id = body["extraction_model_id"]
        save_cfg(cfg)
        _rebuild_registry(cfg)
        return {
            "default_model": cfg.agent.default_model,
            "last_used_model": cfg.agent.last_used_model,
            "classification_model": cfg.agent.classification_model,
            "routing_mode": cfg.agent.routing_mode,
            "embedding_model_id": cfg.graphrag.embedding_model_id,
            "extraction_model_id": cfg.graphrag.extraction_model_id,
        }

    @app.put("/models/roles")
    async def set_model_role(body: ModelRolePayload) -> dict[str, str]:
        from ..config_file import load as load_cfg, save as save_cfg
        cfg = _state["cfg"] or load_cfg()
        new_id = body.model_id or ""
        if body.role == "embedding":
            cfg.graphrag.embedding_model_id = new_id
        elif body.role == "extraction":
            cfg.graphrag.extraction_model_id = new_id
        elif body.role == "classification":
            cfg.agent.classification_model = new_id
        else:
            from fastapi import HTTPException
            raise HTTPException(400, f"Unknown role: {body.role}")
        save_cfg(cfg)
        _state["cfg"] = cfg
        return {"role": body.role, "model_id": new_id}

    from . import transcribe as _transcribe_mod
    _transcribe_mod.register(app)

    return app
