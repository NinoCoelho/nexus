"""Shared turn-launch service — programmatic entry into the chat pipeline.

Encapsulates the pre-turn steps that ``POST /chat/stream`` performs
(parked-form guard, active-turn queueing, size cap, context-window
pre-check with auto-compaction, eager user-message persist, autotitle,
detached ``ChatTurnRunner`` start) so non-HTTP gateways — the Telegram
bot — drive turns with the exact same semantics as the UI: queue-then-
inject, chaining, mid-turn compaction and HITL all behave identically.

The HTTP route keeps its SSE-specific control flow; the context-window
pre-check lives here so both paths share one source of truth.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, TYPE_CHECKING

from ...config_file import load_cached as load_config
from ..events import SessionEvent
from ..session_store import SessionStore

if TYPE_CHECKING:
    from ...agent.loop import Agent
    from ..job_tracker import JobTracker
    from .chat_turn_runner import ChatTurnRunner

log = logging.getLogger(__name__)

_MAX_MESSAGE_CHARS = 50_000


@dataclass
class LaunchOutcome:
    """Result of ``launch_turn``.

    Exactly one of:
    - ``runner`` set, ``queued`` False — a fresh turn was started; events
      flow on the session bus (replay buffer was reset for this turn).
    - ``runner`` set, ``queued`` True — the message was enqueued on the
      active runner (mid-turn injection or chained follow-up); events for
      it arrive on the *already running* stream.
    - ``error`` set — the message was rejected before persisting.
    """

    runner: "ChatTurnRunner | None" = None
    queued: bool = False
    error: str | None = None
    session_id: str = ""
    pre_turn_history: list[Any] = field(default_factory=list)


def precheck_context_window(
    pre_turn_history: list[Any], message: str, model_id: str
) -> tuple[list[Any], dict[str, Any] | None]:
    """Estimate history + incoming message tokens; auto-compact if needed.

    Returns ``(history_maybe_compacted, error_payload_or_None)``. The error
    payload mirrors the shape the /chat/stream SSE ``error`` frame uses.
    Best-effort: any internal failure degrades to "no error".
    """
    _OUTPUT_HEADROOM = 4096
    _TOOLS_AND_SYSTEM_OVERHEAD = 12_000
    try:
        from ...agent.loop.overflow import estimate_tokens as _est_tok
        cfg = load_config()
        ctx_window = 0
        effective_model = model_id or getattr(cfg.agent, "default_model", "")
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
                type("M", (), {"content": message, "tool_calls": []})()
            ])
            total_est = history_tokens + incoming_tokens + _TOOLS_AND_SYSTEM_OVERHEAD
            if total_est > ctx_window - _OUTPUT_HEADROOM:
                from ...agent.loop.compact import auto_compact
                compacted, report = auto_compact(pre_turn_history)
                if report.compacted > 0:
                    new_tokens = (
                        _est_tok(compacted) + incoming_tokens + _TOOLS_AND_SYSTEM_OVERHEAD
                    )
                    if new_tokens <= ctx_window - _OUTPUT_HEADROOM:
                        pre_turn_history = compacted
                        total_est = new_tokens
            if total_est > ctx_window - _OUTPUT_HEADROOM:
                return pre_turn_history, {
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
                }
    except Exception:
        log.debug("pre-send context-window check failed", exc_info=True)
    return pre_turn_history, None


def _default_publish_job_event(store: SessionStore) -> Callable[[str, dict], None]:
    def _publish(kind: str, data: dict[str, Any]) -> None:
        store.publish("__jobs__", SessionEvent(kind=kind, data=data))

    return _publish


async def launch_turn(
    *,
    agent: "Agent",
    store: SessionStore,
    tracker: "JobTracker",
    session: Any,
    message: str,
    model_id: str = "",
    attachment_parts: list[Any] | None = None,
    publish_job_event: Callable[[str, dict], None] | None = None,
    is_voice: bool = False,
) -> LaunchOutcome:
    """Run one chat turn on ``session`` outside of HTTP.

    Mirrors the /chat/stream route's pre-turn pipeline; see module docstring.
    """
    from .chat_turn_runner import ChatTurnRunner, get_running_turn

    if publish_job_event is None:
        publish_job_event = _default_publish_job_event(store)

    # Parked-form guard — an unanswered form owns this session.
    parked_forms = store.list_pending_for_session(session.id, kind="form")
    if parked_forms:
        return LaunchOutcome(
            error=(
                "This chat is waiting for a parked form to be answered "
                "(answer it in the Nexus UI, or use /new to start another chat)."
            ),
            session_id=session.id,
        )

    # Active-turn queueing — never start a parallel loop on one session.
    active = get_running_turn(session.id)
    if active is not None:
        qid = active.enqueue(message)
        if qid is not None:
            return LaunchOutcome(runner=active, queued=True, session_id=session.id)
        # Runner finalized between check and enqueue — fall through.

    if len(message or "") > _MAX_MESSAGE_CHARS:
        return LaunchOutcome(
            error=(
                f"Message too long ({len(message):,} characters). "
                f"Maximum is {_MAX_MESSAGE_CHARS:,} characters."
            ),
            session_id=session.id,
        )

    pre_turn_history = list(session.history)

    pre_turn_history, ctx_err = precheck_context_window(
        pre_turn_history, message, model_id
    )
    if ctx_err is not None:
        return LaunchOutcome(error=ctx_err["detail"], session_id=session.id)

    # Eagerly persist the user message so a crash before the first delta
    # doesn't lose the prompt. Multipart content when attachments ride along.
    try:
        from ...agent.llm import ChatMessage as _CM, Role as _R
        if attachment_parts:
            from ...agent.llm import ContentPart as _CP
            user_content: Any = (
                [_CP(kind="text", text=message)] if message else []
            ) + attachment_parts
            user_msg = _CM(role=_R.USER, content=user_content)
        else:
            user_msg = _CM(role=_R.USER, content=message)
        store.replace_history(session.id, pre_turn_history + [user_msg])
    except Exception:  # noqa: BLE001 — best-effort
        log.exception("pre-turn user message persist failed")

    # LLM autotitle on the first user turn, concurrent with the loop.
    if (
        not pre_turn_history
        and getattr(agent, "_provider_registry", None) is not None
    ):
        from ..routes.chat_stream_helpers import maybe_autotitle_via_llm
        import asyncio

        asyncio.create_task(
            maybe_autotitle_via_llm(
                store=store,
                agent=agent,
                session_id=session.id,
                user_message=message,
            )
        )

    store._latest_input_mode = getattr(store, "_latest_input_mode", {})  # type: ignore[attr-defined]
    store._latest_input_mode[session.id] = "voice" if is_voice else "text"  # type: ignore[attr-defined]
    store._last_global_input_mode = "voice" if is_voice else "text"  # type: ignore[attr-defined]

    turn_job_id = tracker.start(
        type="chat_turn",
        label=message[:80] if message else "Turn",
        session_id=session.id,
        publish_fn=publish_job_event,
    )

    store.clear_replay(session.id)

    runner = ChatTurnRunner(
        agent=agent,
        store=store,
        session_id=session.id,
        message=message,
        context=session.context or "",
        model_id=model_id,
        pre_turn_history=pre_turn_history,
        attachment_parts=attachment_parts,
        resume_working_messages=None,
        tracker=tracker,
        turn_job_id=turn_job_id,
        publish_job_event=publish_job_event,
        is_voice=is_voice,
    )
    runner.start()

    return LaunchOutcome(runner=runner, session_id=session.id, pre_turn_history=pre_turn_history)
