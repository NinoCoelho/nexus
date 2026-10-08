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
    # Structured form of ``error`` when one is available (reason / actions /
    # token counts), so a caller that can render more than a string — a future
    # richer Telegram surface, or an API consumer — isn't limited to the text.
    error_payload: dict[str, Any] | None = None
    session_id: str = ""
    pre_turn_history: list[Any] = field(default_factory=list)


def _resolve_context_window(model_id: str) -> tuple[str, int]:
    """``(effective_model, window)`` — configured, else known, else fallback.

    Never returns 0 for the window. Returning 0 is what used to disable this
    whole gate for any model missing from the registry, which meant the
    newest models got the *least* protection.
    """
    from ...agent.loop.overflow import effective_context_window

    cfg = load_config()
    effective_model = model_id or getattr(cfg.agent, "default_model", "")
    configured = 0
    for entry in cfg.models:
        if entry.id == effective_model or entry.model_name == effective_model:
            configured = int(entry.context_window or 0)
            break
    return effective_model, effective_context_window(effective_model, configured)


async def precheck_context_window(
    pre_turn_history: list[Any],
    message: str,
    model_id: str,
    *,
    provider: Any | None = None,
    session_id: str | None = None,
    attachment_parts: list[Any] | None = None,
) -> tuple[list[Any], dict[str, Any] | None]:
    """Make room for the incoming turn; only error if the *message* can't fit.

    Returns ``(history_maybe_compacted, error_payload_or_None)``. The error
    payload mirrors the shape the /chat/stream SSE ``error`` frame uses.

    This is the gate in front of every turn (web, Telegram, coordinator), so
    it is the one place a user actually got stuck. It used to run a single
    tool-shrink pass, throw that pass away unless it fully solved the problem,
    and then hard-refuse with ``retryable: False`` — on a history of mostly
    prose there was no recovery at all. Now it escalates through the full
    pipeline and finishes with the deterministic trimmer, so history size can
    no longer block a turn:

    1. estimate history + message + attachments against the usable window,
    2. if over budget (or the history is simply too long), run
       ``compact_and_summarize`` and keep the result whenever it is smaller,
       even if it didn't fully solve the problem,
    3. if still over, ``hard_trim`` to force a fit (LLM-free, guaranteed),
    4. error only when the incoming message *by itself* doesn't fit, which
       the user can act on by shortening it.

    Best-effort: any internal failure degrades to "no error" so a bug here
    can never block chat.
    """
    try:
        from ...agent.loop.overflow import (
            check_message_count,
            estimate_tokens as _est_tok,
            usable_tokens,
        )

        effective_model, ctx_window = _resolve_context_window(model_id)
        budget = usable_tokens(ctx_window)

        incoming: list[Any] = [
            type("M", (), {"content": message, "tool_calls": [], "role": "user"})()
        ]
        if attachment_parts:
            incoming.append(
                type("M", (), {"content": list(attachment_parts), "tool_calls": [], "role": "user"})()
            )
        incoming_tokens = _est_tok(incoming)

        history_tokens = _est_tok(pre_turn_history) if pre_turn_history else 0
        total_est = history_tokens + incoming_tokens

        # A long tail of small messages can stay under budget while still
        # carrying hundreds of stale turns, so message count is a trigger too.
        too_many = check_message_count(pre_turn_history)

        if pre_turn_history and (total_est > budget or too_many):
            from ...agent.loop.compact import compact_and_summarize, hard_trim

            reason = "over budget" if total_est > budget else "message count"
            log.info(
                "precheck: history needs compaction (%s): ~%d + %d incoming vs %d usable",
                reason, history_tokens, incoming_tokens, budget,
            )
            try:
                compacted, report = await compact_and_summarize(
                    list(pre_turn_history),
                    context_window=ctx_window,
                    session_id=session_id,
                    model_id=effective_model,
                    provider=provider,
                    strategy="auto",
                    # When only the message count tripped, the zone is still
                    # green and nothing else would act — forcing the summary
                    # is the whole point of the trigger, and collapsing those
                    # turns is what brings the count back down.
                    force_summarize=too_many and total_est <= budget,
                )
            except Exception:  # noqa: BLE001 — fall through to hard_trim
                log.warning("precheck: compact_and_summarize failed", exc_info=True)
                compacted, report = list(pre_turn_history), None

            # Keep any improvement. The old gate discarded a smaller history
            # unless it fully fit, then reported failure — strictly worse than
            # using what it had.
            new_tokens = _est_tok(compacted) if compacted else 0
            if compacted and new_tokens < history_tokens:
                pre_turn_history = compacted
                history_tokens = new_tokens
                total_est = history_tokens + incoming_tokens
                if report is not None:
                    log.info(
                        "precheck: compacted %d→%d tokens (summarized=%s)",
                        report.tokens_before, report.tokens_after, report.summarized,
                    )

            # Deterministic guarantee: if summarization was unavailable or
            # insufficient, force the fit rather than refusing the turn.
            if total_est > budget:
                target = max(0, budget - incoming_tokens)
                if target > 0:
                    trimmed, elided = hard_trim(
                        list(pre_turn_history),
                        target_tokens=target,
                        session_id=session_id,
                    )
                    trimmed_tokens = _est_tok(trimmed) if trimmed else 0
                    if trimmed_tokens < history_tokens:
                        pre_turn_history = trimmed
                        history_tokens = trimmed_tokens
                        total_est = history_tokens + incoming_tokens
                    if elided:
                        log.warning(
                            "precheck: hard-trimmed %d message(s) from session %s "
                            "to fit the context window",
                            elided, session_id,
                        )

        # The only remaining failure is an incoming message that cannot fit on
        # its own — shortening it is something the user can actually do.
        if incoming_tokens > budget:
            return pre_turn_history, {
                "detail": (
                    f"This message is too large for the model's context window on its "
                    f"own (~{incoming_tokens:,} tokens vs {budget:,} usable of "
                    f"{ctx_window:,}). Shorten it, split it across turns, or switch to "
                    f"a model with a larger window."
                ),
                "reason": "message_too_large",
                "retryable": False,
                "status_code": None,
                "actions": ["compact_history", "new_session"],
                "estimated_input_tokens": incoming_tokens,
                "context_window": ctx_window,
            }
        if total_est > budget:
            # Should be unreachable: hard_trim guarantees a fit. Kept as a
            # loud backstop rather than silently handing an oversized payload
            # to the provider.
            log.error(
                "precheck: still over budget after hard_trim "
                "(total=%d, budget=%d, session=%s)",
                total_est, budget, session_id,
            )
            return pre_turn_history, {
                "detail": (
                    f"Could not fit this conversation into the model's context window "
                    f"(~{total_est:,} tokens vs {budget:,} usable). Start a new chat, "
                    f"or set a larger context_window for this model in settings."
                ),
                "reason": "context_overflow",
                "retryable": True,
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
    autotitle_message: str | None = None,
    origin: str = "web",
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
                "(reply to the form prompt, answer it in the Nexus UI, "
                "or use /new to start another chat)."
            ),
            session_id=session.id,
        )

    # Active-turn queueing — never start a parallel loop on one session.
    active = get_running_turn(session.id)
    if active is not None:
        qid = active.enqueue(message, origin=origin)
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

    pre_turn_history, ctx_err = await precheck_context_window(
        pre_turn_history,
        message,
        model_id,
        provider=getattr(agent, "_nexus_provider", None),
        session_id=session.id,
        attachment_parts=attachment_parts,
    )
    if ctx_err is not None:
        # Non-HTTP gateways only render ``error`` as text, so fold the
        # actionable part of the structured payload into the message —
        # otherwise a Telegram user is told the chat is too large with no hint
        # that /compact exists.
        detail = ctx_err["detail"]
        if "compact_history" in (ctx_err.get("actions") or []):
            detail += " Use /compact to shrink this chat, or /new to start a fresh one."
        return LaunchOutcome(
            error=detail, error_payload=ctx_err, session_id=session.id
        )

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
    if not pre_turn_history and getattr(agent, "_provider_registry", None) is not None:
        from ..routes.chat_stream_helpers import maybe_autotitle_via_llm
        import asyncio

        asyncio.create_task(
            maybe_autotitle_via_llm(
                store=store,
                agent=agent,
                session_id=session.id,
                # Group/topic turns prefix the sender ("From Alice (@a):\n\n…")
                # for the agent; the title should describe the message, not
                # the sender — callers can pass the un-prefixed text.
                user_message=autotitle_message if autotitle_message else message,
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
        origin=origin,
    )
    runner.start()

    return LaunchOutcome(runner=runner, session_id=session.id, pre_turn_history=pre_turn_history)
