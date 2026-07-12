"""Streaming chat endpoint: POST /chat/stream.

The event_generator coroutine and all its error-handling/persistence logic
live here to keep chat.py under 300 lines.
Imports the shared in-flight tracking dicts from chat.py so chat_cancel
can cancel a stream task from chat.py's endpoint.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ..deps import get_agent, get_sessions, get_job_tracker
from ..schemas import ChatRequest
from ._sse import keepalive
from ._streaming import TurnAccumulator
from ...agent.context import CURRENT_SESSION_ID
from ...agent.loop import Agent
from ...config_file import load_cached as load_config
from ...redact import redact_sensitive_text
from ..session_store import SessionStore
from ..job_tracker import JobTracker

# Shared with chat.py — the cancel endpoint in chat.py mutates these same dicts
# at runtime (imported once at module load; Python module objects are singletons).
from .chat import _inflight_turns

log = logging.getLogger(__name__)

router = APIRouter()


@router.post("/chat/stream")
async def chat_stream_route(
    req: ChatRequest,
    request: Request,
    a: Agent = Depends(get_agent),
    store: SessionStore = Depends(get_sessions),
    tracker: JobTracker = Depends(get_job_tracker),
) -> StreamingResponse:
    from fastapi import HTTPException, status as _status

    def _publish_job_event(kind: str, data: dict[str, Any]) -> None:
        from ..events import SessionEvent
        store.publish("__jobs__", SessionEvent(kind=kind, data=data))

    # Server-side secret backstop. The UI's pre-send modal is the primary
    # control; this catches messages that bypass the UI (curl, scripts) or
    # API-key-shaped strings the UI's narrower regex set missed. We never
    # 422 — false positives are real, and refusing the message would lose
    # the user's intent. Instead we mask, log, and continue. The UI sets
    # `X-Bypass-Secret-Guard: 1` when the user explicitly chose "Send anyway".
    bypass = request.headers.get("x-bypass-secret-guard") == "1"
    if not bypass:
        original = req.message
        redacted = redact_sensitive_text(original)
        if redacted != original:
            log.warning(
                "secret-shaped string detected in chat input "
                "(session=%s); persisting redacted form",
                req.session_id or "<new>",
            )
            req.message = redacted

    session = store.get_or_create(req.session_id, context=req.context, project_id=req.project_id)

    if getattr(request.app.state, "multi_user", False):
        user_id = getattr(request.state, "user_id", None)
        if user_id:
            request.app.state.user_store.claim_session(session.id, user_id)

    # Block if a form is parked on this session — the agent's previous turn
    # is waiting for an async answer, and starting a new turn now would
    # orphan the parked tool_call. Confirm/text/choice timeouts don't
    # park, so this only fires when a long-running form is outstanding.
    parked_forms = store.list_pending_for_session(session.id, kind="form")
    if parked_forms:
        raise HTTPException(
            status_code=_status.HTTP_409_CONFLICT,
            detail={
                "reason": "parked_form",
                "request_id": parked_forms[0]["request_id"],
                "session_id": session.id,
                "message": (
                    "answer the parked form first via "
                    f"/chat/{session.id}/hitl/{parked_forms[0]['request_id']}/answer"
                ),
            },
        )

    # Always fixed routing — auto mode was removed. Legacy callers passing
    # ``model: "auto"`` are coerced to "use the configured default".
    resolved_model_id = req.model if req.model and req.model != "auto" else ""

    _resume_loom_msgs: list | None = None
    if req.resume_working_messages_json:
        from ...agent.llm import ChatMessage as _LCM, Role as _LR
        _resume_loom_msgs = []
        for _rm in json.loads(req.resume_working_messages_json):
            _tcs = None
            if _rm.get("tool_calls"):
                from loom.types import ToolCall as _TC
                _tcs = [_TC(id=tc["id"], name=tc["name"], arguments=tc["arguments"]) for tc in _rm["tool_calls"]]
            _resume_loom_msgs.append(_LCM(
                role=_LR(_rm["role"]),
                content=_rm["content"],
                tool_calls=_tcs,
                tool_call_id=_rm.get("tool_call_id"),
                name=_rm.get("name"),
            ))

    async def event_generator() -> AsyncIterator[str]:
        # If the user is retrying (hidden-seed marker), roll back the last
        # failed assistant turn from the loaded history so the retry starts
        # from a clean state instead of replaying broken tool_calls.
        _HIDDEN_SEED = "<!-- nx:hidden-seed -->"
        if req.message.startswith(_HIDDEN_SEED) and session.history:
            from ...agent.llm import Role as _R
            while session.history and session.history[-1].role == _R.TOOL:
                session.history.pop()
            if session.history and session.history[-1].role == _R.ASSISTANT:
                session.history.pop()

        # Snapshot the pre-turn history so partial-persistence on
        # abnormal exit can rebuild "history + user + partial assistant"
        # without double-appending.
        pre_turn_history = list(session.history)
        # Bind session context for the duration of the stream so
        # any ask_user call inside the turn knows which session to
        # publish on. The contextvar is local to this coroutine
        # (generators carry their own context) so concurrent
        # streams don't stomp on each other.
        token = CURRENT_SESSION_ID.set(session.id)
        from ...agent.context import ALLOWED_TOOLS
        from ..permissions import allowed_tools_for_role
        user_role = getattr(request.state, "user_role", None)
        _allowed_token = ALLOWED_TOOLS.set(allowed_tools_for_role(user_role))

        turn_job_id = tracker.start(
            type="chat_turn",
            label=req.message[:80] if req.message else "Turn",
            session_id=session.id,
            publish_fn=_publish_job_event,
        )

        # Slash-command fast path: handle /compact, /clear, /title, /usage,
        # /help (and any future commands in the SLASH_COMMANDS registry)
        # without spinning up the agent loop. The handler streams its own
        # delta + done events and replaces the session history.
        from .chat_slash import is_slash_command, dispatch as _slash_dispatch
        slash = is_slash_command(req.message)
        handler = _slash_dispatch(slash) if slash else None
        if handler is not None:
            current = asyncio.current_task()
            if current is not None:
                _inflight_turns[session.id] = current
            try:
                async for chunk in handler(
                    store=store,
                    session_id=session.id,
                    pre_turn_history=pre_turn_history,
                    user_message=req.message,
                ):
                    yield chunk
            finally:
                CURRENT_SESSION_ID.reset(token)
                ALLOWED_TOOLS.reset(_allowed_token)
                tracker.done(turn_job_id, publish_fn=_publish_job_event)
                if _inflight_turns.get(session.id) is current:
                    _inflight_turns.pop(session.id, None)
            return

        # Pre-send size guard: reject oversized messages BEFORE persisting
        # them. Without this, a huge paste gets saved, and every subsequent
        # turn immediately fails the pre-flight overflow check — the session
        # becomes permanently stuck with no UI recovery path.
        _MAX_MESSAGE_CHARS = 50_000
        msg_len = len(req.message or "")
        if msg_len > _MAX_MESSAGE_CHARS:
            yield json.dumps({
                "type": "error",
                "detail": (
                    f"Message too long ({msg_len:,} characters). "
                    f"Maximum is {_MAX_MESSAGE_CHARS:,} characters."
                ),
                "reason": "message_too_large",
                "retryable": False,
                "status_code": None,
                "actions": ["compact_history", "new_session"],
            }) + "\n"
            yield json.dumps({
                "type": "done",
                "session_id": session.id,
                "reply": "",
                "trace": [],
                "skills_touched": [],
                "iterations": 0,
                "messages": list(pre_turn_history),
                "usage": {"input_tokens": 0, "output_tokens": 0, "tool_calls": 0, "model": resolved_model_id},
            }) + "\n"
            return

        # Context-window pre-check: estimate history + incoming message
        # tokens and refuse early if they won't fit. The agent loop's own
        # pre-flight check (overflow.py) runs after this, but this gate
        # prevents the oversized message from being persisted.
        #
        # The estimate covers messages only — tool schemas and system prompt
        # are NOT included in estimate_tokens().  We add a flat overhead
        # constant to approximate their cost (~12K for ~46 tool schemas +
        # system prompt + protocol framing).
        _OUTPUT_HEADROOM = 4096
        _TOOLS_AND_SYSTEM_OVERHEAD = 12_000
        try:
            from ...agent.loop.overflow import estimate_tokens as _est_tok
            cfg = load_config()
            ctx_window = 0
            effective_model = resolved_model_id or getattr(cfg.agent, "default_model", "")
            for entry in cfg.models:
                if entry.id == effective_model or entry.model_name == effective_model:
                    ctx_window = int(entry.context_window or 0)
                    break
            if ctx_window == 0:
                from ...agent.loop.overflow import known_context_window as _kcw
                ctx_window = _kcw(effective_model)
            if ctx_window > 0 and pre_turn_history:
                history_tokens = _est_tok(pre_turn_history)
                incoming_tokens = _est_tok([
                    type("M", (), {"content": req.message, "tool_calls": []})()
                ])
                total_est = history_tokens + incoming_tokens + _TOOLS_AND_SYSTEM_OVERHEAD
                if total_est > ctx_window - _OUTPUT_HEADROOM:
                    from ...agent.loop.compact import auto_compact
                    compacted, report = auto_compact(pre_turn_history)
                    if report.compacted > 0:
                        new_tokens = _est_tok(compacted) + incoming_tokens + _TOOLS_AND_SYSTEM_OVERHEAD
                        if new_tokens <= ctx_window - _OUTPUT_HEADROOM:
                            pre_turn_history = compacted
                            total_est = new_tokens
                if total_est > ctx_window - _OUTPUT_HEADROOM:
                    yield json.dumps({
                        "type": "error",
                        "detail": (
                            f"Sending this message would exceed the model's context window "
                            f"(~{total_est:,} tokens needed vs "
                            f"{ctx_window:,} available). Compact the conversation or start a new session."
                        ),
                        "reason": "message_too_large",
                        "retryable": False,
                        "status_code": None,
                        "actions": ["compact_history", "new_session"],
                        "estimated_input_tokens": total_est,
                        "context_window": ctx_window,
                    }) + "\n"
                    yield json.dumps({
                        "type": "done",
                        "session_id": session.id,
                        "reply": "",
                        "trace": [],
                        "skills_touched": [],
                        "iterations": 0,
                        "messages": list(pre_turn_history),
                        "usage": {"input_tokens": 0, "output_tokens": 0, "tool_calls": 0, "model": resolved_model_id},
                    }) + "\n"
                    return
        except Exception:
            log.debug("pre-send context-window check failed", exc_info=True)

        # Eagerly persist the user message so a crash between POST
        # and the first delta doesn't lose the prompt the user typed.
        # When the request carries attachments, persist a multipart
        # ``content`` list so reload-from-DB still shows the image/audio/
        # document refs alongside the text.
        attachment_parts: list[Any] = []
        try:
            from ...agent.llm import ChatMessage as _CM, ContentPart as _CP, Role as _R
            from ...multimodal import sniff_mime as _sniff_mime

            def _classify_kind(mime: str) -> str:
                if mime.startswith("image/"):
                    return "image"
                if mime.startswith("audio/"):
                    return "audio"
                return "document"

            for att in (req.attachments or []):
                mime = att.mime_type or _sniff_mime(att.vault_path)
                attachment_parts.append(
                    _CP(
                        kind=_classify_kind(mime),
                        vault_path=att.vault_path,
                        mime_type=mime,
                    )
                )
            if attachment_parts:
                user_content: Any = (
                    [_CP(kind="text", text=req.message)] if req.message else []
                ) + attachment_parts
                user_msg = _CM(role=_R.USER, content=user_content)
            else:
                user_msg = _CM(role=_R.USER, content=req.message)
            store.replace_history(session.id, pre_turn_history + [user_msg])
        except Exception:  # noqa: BLE001 — best-effort
            log.exception("pre-turn user message persist failed")

        # Schedule LLM autotitle **concurrent** with the agent loop on the
        # first user turn. Real provider calls have independent state, so
        # this doesn't disturb the agent's own LLM session — by the time the
        # agent emits `done` (typically 2–10 s) the small ≤32-token title
        # call has usually completed and the renamed title is in the DB,
        # ready for the UI's post-`done` sidebar refresh. We gate on
        # `provider_registry` so test stubs (FakeProvider with a scripted
        # queue and no registry) skip the call and aren't disrupted.
        if not pre_turn_history and getattr(a, "_provider_registry", None) is not None:
            from .chat_stream_helpers import maybe_autotitle_via_llm
            asyncio.create_task(
                maybe_autotitle_via_llm(
                    store=store,
                    agent=a,
                    session_id=session.id,
                    user_message=req.message,
                )
            )

        # ── Input mode tracking ──────────────────────────────────────────────
        # Stash the input mode so the notify_user tool handler knows whether
        # to TTS the message. Voice ack logic (start ack, speculative
        # completion ack) lives in the ChatTurnRunner.
        store._latest_input_mode = getattr(store, "_latest_input_mode", {})  # type: ignore[attr-defined]
        store._latest_input_mode[session.id] = req.input_mode  # type: ignore[attr-defined]
        store._last_global_input_mode = req.input_mode  # type: ignore[attr-defined]
        is_voice = req.input_mode == "voice"

        # ── Detached turn runner ─────────────────────────────────────────────
        # The agent loop runs as a detached asyncio.Task that survives client
        # disconnects (machine sleep, tab close). This handler subscribes to
        # the session bus and forwards events to the SSE response. When the
        # client disconnects, only the subscriber dies — the runner keeps
        # going. A reconnecting client can resume via GET /chat/{sid}/turn/stream.
        store.clear_replay(session.id)

        from ..services.chat_turn_runner import ChatTurnRunner
        runner = ChatTurnRunner(
            agent=a,
            store=store,
            session_id=session.id,
            message=req.message,
            context=session.context or "",
            model_id=resolved_model_id,
            pre_turn_history=pre_turn_history,
            attachment_parts=attachment_parts or None,
            resume_working_messages=_resume_loom_msgs,
            tracker=tracker,
            turn_job_id=turn_job_id,
            publish_job_event=_publish_job_event,
            is_voice=is_voice,
        )
        runner.start()

        # ── Subscribe to bus and forward events to SSE ───────────────────────
        acc_sub = TurnAccumulator()
        try:
            async for sevent in keepalive(
                store.subscribe_with_replay(session.id),
                interval=15.0,
            ):
                if sevent is None:
                    yield ": ping\n\n"
                    continue
                for frame in acc_sub.process_event(sevent.data):
                    yield frame
                if sevent.data.get("type") == "done":
                    break
        except asyncio.CancelledError:
            # Client disconnected. The subscriber coroutine dies here;
            # the ChatTurnRunner keeps running in the background.
            pass
        finally:
            try:
                CURRENT_SESSION_ID.reset(token)
                ALLOWED_TOOLS.reset(_allowed_token)
            except ValueError:
                log.debug("CURRENT_SESSION_ID reset across contexts")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
