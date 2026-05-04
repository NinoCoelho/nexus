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

from ..deps import get_agent, get_sessions
from ..schemas import ChatRequest
from ...agent.context import CURRENT_SESSION_ID
from ...agent.llm import LLMTransportError, MalformedOutputError
from ...agent.loop import Agent
from ...config_file import load as load_config
from ...redact import redact_sensitive_text
from ...voice_ack import (
    _AckTrigger,
    emit_completion_ack,
)
from ..session_store import SessionStore

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
) -> StreamingResponse:
    from fastapi import HTTPException, status as _status

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

    session = store.get_or_create(req.session_id, context=req.context)

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
                if _inflight_turns.get(session.id) is current:
                    _inflight_turns.pop(session.id, None)
            return

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
        ack_active = (
            is_voice
            and tts_cfg.enabled
            and tts_cfg.ack_enabled
            and getattr(a, "_provider_registry", None) is not None
        )
        log.warning(
            "[chat_stream] voice_ack ack_active=%s (is_voice=%s tts.enabled=%s ack_enabled=%s registry=%s)",
            ack_active, is_voice, tts_cfg.enabled, tts_cfg.ack_enabled,
            getattr(a, "_provider_registry", None) is not None,
        )

        # Speculative completion-ack kickoff: when the agent has streamed
        # ~80+ words of content and is in the middle of writing its final
        # reply, fire the summary call NOW so the audio is ready (or
        # nearly ready) by the time `done` arrives. Without this, audio
        # plays 5-15s AFTER the visible text is fully written — the user
        # has been complaining about exactly that lag.
        SPECULATIVE_WORD_THRESHOLD = 80
        completion_task: asyncio.Task | None = None

        try:
            async for event in a.run_turn_stream(
                req.message,
                history=session.history,
                context=session.context,
                session_id=session.id,
                model_id=resolved_model_id,
                attachments=attachment_parts or None,
            ):
                etype = event.get("type")

                if etype == "delta":
                    accumulated_text += event.get("text", "")
                    # Speculative summary kickoff (see comment above).
                    if (
                        ack_active
                        and completion_task is None
                        and len(accumulated_text.split()) >= SPECULATIVE_WORD_THRESHOLD
                    ):
                        # Snapshot accumulated_text now (str is immutable;
                        # later appends won't mutate the captured value).
                        snapshot = accumulated_text
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
                    yield f"event: delta\ndata: {json.dumps({'text': event['text']})}\n\n"

                elif etype == "thinking":
                    # Chain-of-thought from reasoning models (Ollama GLM,
                    # DeepSeek-R1, …). Forwarded as its own SSE channel so
                    # the UI can render it collapsed without it polluting
                    # the assistant message body.
                    yield f"event: thinking\ndata: {json.dumps({'text': event.get('text', '')})}\n\n"

                elif etype == "limit_reached":
                    partial_status = "iteration_limit"
                    yield f"event: limit_reached\ndata: {json.dumps({'iterations': event.get('iterations', 0)})}\n\n"

                elif etype == "reconnecting":
                    # Auto-retry hint from Agent.run_turn_stream during
                    # backoff. Forwarded so the UI can show a small
                    # "Reconnecting (attempt N/M)…" indicator while the
                    # backend waits to restart the loom turn. The UI
                    # clears the indicator on the next delta/tool event.
                    yield (
                        f"event: reconnecting\ndata: "
                        f"{json.dumps({'attempt': event.get('attempt', 1), 'max_attempts': event.get('max_attempts', 1), 'delay_seconds': event.get('delay_seconds', 0), 'reason': event.get('reason', '')})}"
                        f"\n\n"
                    )

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

                    # Voice ack: fire completion ack as a fire-and-forget
                    # task — but ONLY if the speculative kickoff above
                    # didn't already fire. When it did, we'd be
                    # double-publishing two completion summaries that
                    # the UI player would race against each other.
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
                    # Mid-stream structured error from the agent loop
                    # (e.g. an upstream failure after content was already
                    # streamed, so retry was impossible, or a truncation /
                    # empty-response signal emitted by loop.py before the
                    # done terminator). Forward the classifier's fields so
                    # the UI can show a richer message without re-parsing
                    # the detail string — and stamp partial_status so the
                    # persisted assistant prefix matches the banner.
                    reason = event.get("reason")
                    # Keep partial_status in sync with classified reasons
                    # so the persisted assistant prefix reflects the real
                    # failure (was previously a tight allowlist that let
                    # rate_limit / auth_error / transport / bad_request
                    # fall through as "no status" — and the UI's done
                    # handler then dropped the banner entirely).
                    if reason and reason not in ("interrupted", "cancelled"):
                        partial_status = reason
                    # Log the forwarded payload so daemon-side debugging
                    # has the exact text the UI's banner is rendering.
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
                    # Forward overflow-specific fields so the UI can offer a
                    # "Compact history" button instead of a bare "Retry".
                    for k in ("likely_cause", "estimated_input_tokens",
                              "context_window", "actions"):
                        if k in event:
                            err_payload[k] = event[k]
                    yield f"event: error\ndata: {json.dumps(err_payload)}\n\n"
        except (LLMTransportError, MalformedOutputError) as exc:
            partial_status = "llm_error"
            try:
                from ...error_classifier import classify_api_error as _c
                _reason = _c(exc).reason.value
                if _reason == "timeout":
                    partial_status = "upstream_timeout"
            except Exception:  # noqa: BLE001
                pass
            log.warning(
                "chat_stream LLM call failed: %s (status=%s)",
                exc, getattr(exc, "status_code", None),
            )
            detail = str(exc)
            reason = None
            retryable = None
            status_code = getattr(exc, "status_code", None)
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
            err_payload = {
                "detail": detail,
                "reason": reason,
                "retryable": retryable,
                "status_code": status_code,
            }
            yield f"event: error\ndata: {json.dumps(err_payload)}\n\n"
            # Critical: emit a terminator `done` so the UI marks the turn
            # as finished. Without this the SSE stream just closes, the
            # client treats it as "interrupted", and its recovery path
            # reloads session history from the DB — wiping the partial
            # banner we just rendered. The cancelled / catch-all branches
            # below already do this; this branch was missing it.
            yield (
                f"event: done\ndata: "
                f"{json.dumps({'session_id': session.id, 'reply': '', 'trace': [], 'skills_touched': [], 'iterations': 0})}"
                f"\n\n"
            )
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
            from .chat_stream_helpers import persist_stream_turn
            persist_stream_turn(
                store=store,
                session_id=session.id,
                final_messages=final_messages,
                pre_turn_history=pre_turn_history,
                user_message=req.message,
                accumulated_text=accumulated_text,
                accumulated_tools=accumulated_tools,
                partial_status=partial_status,
            )
            # SSE consumer disconnects mid-stream cancel the generator
            # from a different async context; CURRENT_SESSION_ID then
            # refuses the reset with a ValueError that aborts the rest
            # of cleanup. Best-effort here.
            try:
                CURRENT_SESSION_ID.reset(token)
            except ValueError:
                log.debug("CURRENT_SESSION_ID reset across contexts")
            if _inflight_turns.get(session.id) is current:
                _inflight_turns.pop(session.id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
