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
from ._streaming import TurnAccumulator, build_done_sse, build_error_sse
from ...agent.context import CURRENT_SESSION_ID
from ...agent.llm import LLMTransportError, MalformedOutputError
from ...agent.loop import Agent
from ...config_file import load as load_config
from ...redact import redact_sensitive_text
from ...voice_ack import (
    _AckTrigger,
    emit_completion_ack,
    emit_start_ack,
)
from ..session_store import SessionStore
from ..job_tracker import JobTracker

# Shared with chat.py — the cancel endpoint in chat.py mutates these same dicts
# at runtime (imported once at module load; Python module objects are singletons).
from .chat import _inflight_turns, _user_cancelled, _trajectory_logger

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
        acc = TurnAccumulator()
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
        current = asyncio.current_task()
        if current is not None:
            _inflight_turns[session.id] = current

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

        # ── Voice acknowledgment plumbing ────────────────────────────────────
        # Only kicks in for voice-input turns. There's NO programmatic
        # start ack anymore — the agent itself uses the `notify_user`
        # tool when it wants to give the user a status update mid-turn
        # (the tool routes to TTS when input_mode is voice). The
        # completion ack still fires on `done` to summarize the reply.
        is_voice = req.input_mode == "voice"
        # Stash on session.context so the notify_user tool handler knows
        # whether to TTS the message or just surface it as a toast.
        try:
            session.context = (session.context or "") + ""  # no-op touch
        except Exception:  # noqa: BLE001
            pass
        store._latest_input_mode = getattr(store, "_latest_input_mode", {})  # type: ignore[attr-defined]
        store._latest_input_mode[session.id] = req.input_mode  # type: ignore[attr-defined]
        # Also track the last *global* input_mode so notify_user fired
        # from sessions we didn't see (vault dispatch, kanban-card spawn,
        # etc.) can fall back to "what the user has been doing recently"
        # instead of always defaulting to text.
        store._last_global_input_mode = req.input_mode  # type: ignore[attr-defined]
        log.warning(
            "[chat_stream] sess=%s input_mode=%r (is_voice=%s)",
            session.id, req.input_mode, is_voice,
        )

        tts_cfg = load_config().tts
        # ack_mode="always" → fire on every turn; "voice" → voice-input only.
        ack_wanted = is_voice or tts_cfg.ack_mode == "always"
        ack_active = (
            ack_wanted
            and tts_cfg.enabled
            and tts_cfg.ack_enabled
            and getattr(a, "_provider_registry", None) is not None
        )
        log.warning(
            "[chat_stream] voice_ack ack_active=%s (is_voice=%s ack_wanted=%s ack_mode=%s tts.enabled=%s ack_enabled=%s registry=%s)",
            ack_active, is_voice, ack_wanted, tts_cfg.ack_mode,
            tts_cfg.enabled, tts_cfg.ack_enabled,
            getattr(a, "_provider_registry", None) is not None,
        )

        # Fire a spoken start-ack for voice-input turns so the user
        # hears an immediate contextual acknowledgment while the agent
        # loop processes. Runs as a fire-and-forget task concurrent
        # with the main agent loop.
        if is_voice and ack_active:
            asyncio.create_task(emit_start_ack(
                agent=a, store=store,
                trigger=_AckTrigger(
                    user_text=req.message,
                    session_id=session.id,
                ),
                cfg=load_config(),
            ))

        # Speculative completion-ack kickoff: when the agent has streamed
        # ~80+ words of content and is in the middle of writing its final
        # reply, fire the summary call NOW so the audio is ready (or
        # nearly ready) by the time `done` arrives. Without this, audio
        # plays 5-15s AFTER the visible text is fully written — the user
        # has been complaining about exactly that lag.
        SPECULATIVE_WORD_THRESHOLD = 80
        completion_task: asyncio.Task | None = None

        try:
            async for event in keepalive(
                a.run_turn_stream(
                    req.message,
                    history=session.history,
                    context=session.context,
                    session_id=session.id,
                    model_id=resolved_model_id,
                    attachments=attachment_parts or None,
                ),
                interval=15.0,
            ):
                if event is None:
                    yield ": ping\n\n"
                    continue
                etype = event.get("type")

                if etype == "delta":
                    acc.accumulated_text += event.get("text", "")
                    if (
                        ack_active
                        and completion_task is None
                        and len(acc.accumulated_text.split()) >= SPECULATIVE_WORD_THRESHOLD
                    ):
                        snapshot = acc.accumulated_text
                        log.warning(
                            "[chat_stream] kicking off speculative completion ack at %d words",
                            len(snapshot.split()),
                        )
                        completion_task = asyncio.create_task(emit_completion_ack(
                            agent=a, store=store,
                            trigger=_AckTrigger(
                                user_text=req.message,
                                session_id=session.id,
                                full_reply=snapshot,
                            ),
                            cfg=load_config(),
                        ))
                    for frame in acc.process_event(event):
                        yield frame

                elif etype == "done":
                    usage = event.get("usage") or {}
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
                        from .chat_stream_helpers import log_stream_trajectory
                        log_stream_trajectory(
                            trajectory_logger=_trajectory_logger,
                            session_id=session.id,
                            turn_index=len(session.history) // 2,
                            user_message=req.message,
                            history_length=len(session.history),
                            context=req.context or "",
                            reply_text=event.get("reply", ""),
                            model=usage.get("model") or "",
                            iterations=event.get("iterations", 0),
                            input_tokens=int(usage.get("input_tokens") or 0),
                            output_tokens=int(usage.get("output_tokens") or 0),
                            tool_calls=int(usage.get("tool_calls") or 0),
                        )
                    done_payload = {
                        "session_id": event.get("session_id") or session.id,
                        "reply": event.get("reply", ""),
                        "trace": event.get("trace", []),
                        "skills_touched": event.get("skills_touched", []),
                        "iterations": event.get("iterations", 0),
                        "usage": usage,
                        "model": usage.get("model") or resolved_model_id,
                    }
                    yield f"event: done\ndata: {json.dumps(done_payload)}\n\n"

                    if ack_active and completion_task is None:
                        asyncio.create_task(emit_completion_ack(
                            agent=a, store=store,
                            trigger=_AckTrigger(
                                user_text=req.message,
                                session_id=session.id,
                                full_reply=event.get("reply", ""),
                            ),
                            cfg=load_config(),
                        ))
                    elif completion_task is not None:
                        log.warning(
                            "[chat_stream] speculative ack already running — "
                            "skipping post-done emit (snapshot may be ~%d words "
                            "vs final %d words)",
                            SPECULATIVE_WORD_THRESHOLD,
                            len(event.get("reply", "").split()),
                        )

                elif etype == "error":
                    reason = event.get("reason")
                    if reason and reason not in ("interrupted", "cancelled"):
                        acc.partial_status = reason
                    log.warning(
                        "chat_stream forwarding error to UI: reason=%r status=%r detail=%r",
                        reason,
                        event.get("status_code"),
                        (event.get("detail") or "")[:300],
                    )
                    err_payload = {
                        "detail": event.get("detail", ""),
                        "reason": reason,
                        "retryable": event.get("retryable"),
                        "status_code": event.get("status_code"),
                    }
                    for k in ("likely_cause", "estimated_input_tokens",
                              "context_window", "actions"):
                        if k in event:
                            err_payload[k] = event[k]
                    yield f"event: error\ndata: {json.dumps(err_payload)}\n\n"

                else:
                    for frame in acc.process_event(event):
                        yield frame
        except (LLMTransportError, MalformedOutputError) as exc:
            acc.partial_status = "llm_error"
            detail = str(exc)
            reason = None
            retryable = None
            status_code = getattr(exc, "status_code", None)
            try:
                from ...error_classifier import classify_api_error, is_budget_exceeded, budget_exceeded_detail
                if is_budget_exceeded(exc):
                    acc.partial_status = "budget_exceeded"
                    reason = "budget_exceeded"
                    retryable = False
                    bd = budget_exceeded_detail(exc)
                    if bd:
                        detail = bd
                else:
                    _reason = classify_api_error(exc).reason.value
                    if _reason == "timeout":
                        acc.partial_status = "upstream_timeout"
            except Exception:  # noqa: BLE001
                pass
            log.warning(
                "chat_stream LLM call failed: %s (status=%s)",
                exc, getattr(exc, "status_code", None),
            )
            if not detail or detail == str(exc):
                detail = str(exc)
            if reason is None:
                try:
                    from ...error_classifier import classify_api_error
                    classified = classify_api_error(exc)
                    reason = classified.reason.value
                    retryable = classified.retryable
                    if classified.user_facing_summary:
                        detail = f"{classified.user_facing_summary} ({detail})"
                except Exception:
                    pass
            try:
                store.log_error(
                    session.id,
                    reason or "llm_error",
                    message=detail[:2000],
                    status_code=status_code,
                    model=session.model_id if hasattr(session, 'model_id') else None,
                    retryable=retryable or False,
                )
            except Exception:  # noqa: BLE001
                pass
            yield build_error_sse(detail=detail, reason=reason, retryable=retryable, status_code=status_code)
            yield build_done_sse(session_id=session.id)
        except asyncio.CancelledError:
            acc.partial_status = "cancelled" if session.id in _user_cancelled else "interrupted"
            _user_cancelled.discard(session.id)
            yield build_error_sse(detail="cancelled by user", reason="cancelled")
            yield build_done_sse(session_id=session.id)
        except Exception as exc:
            acc.partial_status = "crashed"
            log.exception("chat_stream crashed")
            yield build_error_sse(detail=f"{type(exc).__name__}: {exc}")
            yield build_done_sse(session_id=session.id)
        finally:
            from .chat_stream_helpers import persist_stream_turn
            persist_stream_turn(
                store=store,
                session_id=session.id,
                final_messages=acc.final_messages,
                pre_turn_history=pre_turn_history,
                user_message=req.message,
                accumulated_text=acc.accumulated_text,
                accumulated_tools=acc.accumulated_tools,
                partial_status=acc.partial_status,
            )
            tracker.done(turn_job_id, publish_fn=_publish_job_event)
            try:
                CURRENT_SESSION_ID.reset(token)
                ALLOWED_TOOLS.reset(_allowed_token)
            except ValueError:
                log.debug("CURRENT_SESSION_ID reset across contexts")
            if _inflight_turns.get(session.id) is current:
                _inflight_turns.pop(session.id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
