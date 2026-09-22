"""Detached chat-turn runner.

Decouples the agent loop from the SSE request lifetime so a chat turn
survives client disconnects (machine sleep, tab close, network blip).

The runner is spawned as a detached ``asyncio.Task`` by ``POST /chat/stream``.
It drives ``Agent.run_turn_stream``, publishes every event to the session
pub-sub bus, and persists the final/partial result. The HTTP handler
(and the resume endpoint ``GET /chat/{sid}/turn/stream``) merely subscribe
to the bus — they forward events to the SSE response without driving the loop.

When the SSE subscriber dies (client disconnect), only the subscriber task
is cancelled; the runner keeps going. A reconnecting client calls
``GET /chat/{sid}/turn/stream``, which subscribes with replay and picks up
the full turn from the beginning.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ..events import SessionEvent
from ...agent.context import CURRENT_SESSION_ID
from ..routes._streaming import TurnAccumulator

if TYPE_CHECKING:
    from ...agent.loop import Agent
    from ..session_store import SessionStore
    from ..job_tracker import JobTracker

log = logging.getLogger(__name__)


@dataclass
class QueuedInput:
    """A user message received while a turn was already running."""

    qid: str
    text: str


@dataclass
class ChatTurnRunner:
    """Drives one agent turn as a detached task.

    Attributes are set by the caller (chat_stream route) before ``start()``.
    """

    agent: "Agent"
    store: "SessionStore"
    session_id: str
    message: str
    context: str
    model_id: str
    pre_turn_history: list[Any]
    attachment_parts: list[Any] | None
    resume_working_messages: list[Any] | None
    tracker: "JobTracker"
    turn_job_id: str
    publish_job_event: Any  # callable
    is_voice: bool = False
    acc: TurnAccumulator = field(default_factory=TurnAccumulator)
    task: asyncio.Task[Any] | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Follow-up messages sent while this turn is running. Injected at the
    # next tool-batch boundary (``drain_pending``) or chained as follow-up
    # turns when the current turn ends first (``_next_queued``).
    queue: list[QueuedInput] = field(default_factory=list)
    _finalized: bool = False

    # ── Registry ────────────────────────────────────────────────────────

    def start(self) -> asyncio.Task[Any]:
        """Spawn the runner as a detached task and register it."""
        self.task = asyncio.create_task(self._run(), name=f"chat-turn-{self.session_id}")
        _running_turns[self.session_id] = self
        return self.task

    def cancel(self) -> bool:
        """Cancel the detached task. Returns True if a live task was cancelled."""
        if self.task is not None and not self.task.done():
            self.task.cancel()
            return True
        return False

    # ── Queue (messages sent while this turn runs) ──────────────────────

    def enqueue(self, text: str) -> str | None:
        """Queue a follow-up message for mid-turn injection or chaining.

        Returns the queue id, or ``None`` when the runner already
        finalized (the caller should start a normal turn instead).
        Race-free: the runner's final drain check (``_next_queued``) sets
        ``_finalized`` without any await in between, so on the event loop
        an ``enqueue`` either lands before that check (and is chained)
        or sees ``_finalized`` and bails — an acked message is never lost.
        """
        if self._finalized:
            return None
        qid = uuid4().hex[:12]
        self.queue.append(QueuedInput(qid=qid, text=text))
        return qid

    def remove_queued(self, qid: str) -> bool:
        """Drop a still-queued message (user clicked the chip's × )."""
        for i, item in enumerate(self.queue):
            if item.qid == qid:
                del self.queue[i]
                self._publish({
                    "type": "queue_removed",
                    "qid": qid,
                    "session_id": self.session_id,
                    "reason": "removed",
                })
                return True
        return False

    def drain_pending(self) -> list[dict[str, str]]:
        """Drain the whole queue for mid-turn injection.

        Called by the agent loop at tool-batch boundaries (via the
        ``drain_pending_inputs`` callback) — the drained messages are
        appended to the working context as user messages.
        """
        items = [{"qid": q.qid, "text": q.text} for q in self.queue]
        self.queue.clear()
        return items

    def _drop_queue(self, reason: str) -> None:
        for item in self.queue:
            self._publish({
                "type": "queue_removed",
                "qid": item.qid,
                "session_id": self.session_id,
                "reason": reason,
            })
        self.queue.clear()

    def _next_queued(self) -> QueuedInput | None:
        """Pop the next queued input, or finalize the runner (see enqueue)."""
        if self.queue:
            return self.queue.pop(0)
        self._finalized = True
        return None

    def _begin_chained_turn(self, nxt: QueuedInput) -> None:
        """Re-arm the runner for a follow-up turn with a queued message."""
        from ...agent.llm import ChatMessage as _CM, Role as _R

        session = self.store.get(self.session_id)
        fresh_history = list(session.history) if session else []
        try:
            self.store.replace_history(
                self.session_id, fresh_history + [_CM(role=_R.USER, content=nxt.text)]
            )
        except Exception:  # noqa: BLE001 — best-effort eager persist
            log.exception("queued-turn user message persist failed")
        self.pre_turn_history = fresh_history
        self.message = nxt.text
        if session is not None and session.context:
            self.context = session.context
        self.attachment_parts = None
        self.resume_working_messages = None
        self.acc = TurnAccumulator()
        self.started_at = datetime.now(timezone.utc)

    # ── Main loop ───────────────────────────────────────────────────────

    async def _run(self) -> None:
        token = CURRENT_SESSION_ID.set(self.session_id)
        self.store._trace_suppressed.add(self.session_id)
        try:
            await self._drive_loop()
            # Chained turns: queued messages that arrived during the turn
            # but were never injected (no tool-batch boundary) run as
            # follow-up turns on the same runner, so the open SSE stream
            # keeps flowing past the first `done`.
            while True:
                nxt = self._next_queued()
                if nxt is None:
                    break
                self._publish({
                    "type": "user_injected",
                    "qid": nxt.qid,
                    "text": nxt.text,
                    "session_id": self.session_id,
                })
                self._begin_chained_turn(nxt)
                await self._drive_loop()
        except asyncio.CancelledError:
            self.acc.partial_status = "cancelled"
            self._drop_queue("cancelled")
            self._publish_terminal_error(
                detail="cancelled by user",
                reason="cancelled",
            )
        except Exception as exc:
            log.exception("chat_turn_runner crashed")
            self.acc.partial_status = "crashed"
            self._drop_queue("turn_error")
            self._publish_terminal_error(
                detail=f"{type(exc).__name__}: {exc}",
            )
        finally:
            self._persist()
            self.tracker.done(self.turn_job_id, publish_fn=self.publish_job_event)
            self.store._trace_suppressed.discard(self.session_id)
            # Terminal marker: subscribers keep the SSE open past `done`
            # (chained turns) and only close on this event.
            self._publish({"type": "turn_settled", "session_id": self.session_id})
            try:
                CURRENT_SESSION_ID.reset(token)
            except ValueError:
                log.debug("CURRENT_SESSION_ID reset across contexts")
            if _running_turns.get(self.session_id) is self:
                _running_turns.pop(self.session_id, None)

    async def _drive_loop(self) -> None:
        from ...config_file import load_cached as load_config
        from ...agent.llm import LLMTransportError, MalformedOutputError
        from ..routes._streaming import build_done_sse  # noqa: F401 — not used here but documents format
        from ...voice_ack import _AckTrigger, emit_completion_ack, emit_start_ack

        cfg = load_config()
        tts_cfg = cfg.tts
        ack_wanted = self.is_voice or tts_cfg.ack_mode == "always"
        ack_active = (
            ack_wanted
            and tts_cfg.enabled
            and tts_cfg.ack_enabled
            and getattr(self.agent, "_provider_registry", None) is not None
        )

        if self.is_voice and ack_active:
            asyncio.create_task(emit_start_ack(
                agent=self.agent, store=self.store,
                trigger=_AckTrigger(
                    user_text=self.message,
                    session_id=self.session_id,
                ),
                cfg=cfg,
            ))

        SPECULATIVE_WORD_THRESHOLD = 80
        completion_task: asyncio.Task[Any] | None = None

        try:
            async for event in self.agent.run_turn_stream(
                self.message,
                history=self.pre_turn_history,
                context=self.context,
                session_id=self.session_id,
                model_id=self.model_id or None,
                attachments=self.attachment_parts or None,
                resume_working_messages=self.resume_working_messages,
                drain_pending_inputs=self.drain_pending,
            ):
                etype = event.get("type")

                if etype == "delta":
                    if (
                        ack_active
                        and completion_task is None
                        and len(self.acc.accumulated_text.split()) >= SPECULATIVE_WORD_THRESHOLD
                    ):
                        snapshot = self.acc.accumulated_text
                        completion_task = asyncio.create_task(emit_completion_ack(
                            agent=self.agent, store=self.store,
                            trigger=_AckTrigger(
                                user_text=self.message,
                                session_id=self.session_id,
                                full_reply=snapshot,
                            ),
                            cfg=cfg,
                        ))
                    self.acc.process_event(event)
                    self._publish(event)

                elif etype == "done":
                    self.acc.final_messages = event.get("messages")
                    usage = event.get("usage") or {}
                    try:
                        self.store.bump_usage(
                            self.session_id,
                            model=usage.get("model"),
                            input_tokens=int(usage.get("input_tokens") or 0),
                            output_tokens=int(usage.get("output_tokens") or 0),
                            tool_calls=int(usage.get("tool_calls") or 0),
                        )
                    except Exception:
                        log.exception("bump_usage failed")
                    done_event = dict(event)
                    done_event["model"] = usage.get("model") or self.model_id
                    self._publish(done_event)
                    if ack_active and completion_task is None:
                        asyncio.create_task(emit_completion_ack(
                            agent=self.agent, store=self.store,
                            trigger=_AckTrigger(
                                user_text=self.message,
                                session_id=self.session_id,
                                full_reply=event.get("reply", ""),
                            ),
                            cfg=cfg,
                        ))

                elif etype == "error":
                    reason = event.get("reason")
                    if reason and reason not in ("interrupted", "cancelled"):
                        self.acc.partial_status = reason
                    self._publish(event)

                else:
                    self.acc.process_event(event)
                    self._publish(event)

        except (LLMTransportError, MalformedOutputError) as exc:
            self.acc.partial_status = "llm_error"
            detail = str(exc)
            reason: str | None = None
            retryable: bool | None = None
            status_code = getattr(exc, "status_code", None)
            try:
                from ...error_classifier import (
                    classify_api_error,
                    is_budget_exceeded,
                    budget_exceeded_detail,
                )
                if is_budget_exceeded(exc):
                    self.acc.partial_status = "budget_exceeded"
                    reason = "budget_exceeded"
                    retryable = False
                    bd = budget_exceeded_detail(exc)
                    if bd:
                        detail = bd
                else:
                    _reason = classify_api_error(exc).reason.value
                    if _reason == "timeout":
                        self.acc.partial_status = "upstream_timeout"
            except Exception:
                pass
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
                self.store.log_error(
                    self.session_id,
                    reason or "llm_error",
                    message=detail[:2000],
                    status_code=status_code,
                    retryable=retryable or False,
                )
            except Exception:
                pass
            self._publish_terminal_error(
                detail=detail,
                reason=reason,
                retryable=retryable,
                status_code=status_code,
            )

    # ── Publishing helpers ──────────────────────────────────────────────

    def _publish(self, event: dict[str, Any]) -> None:
        """Publish a run_turn_stream event to the session bus."""
        self.store.publish(
            self.session_id,
            SessionEvent(kind=event.get("type", "message"), data=event),
        )

    def _publish_terminal_error(
        self,
        *,
        detail: str,
        reason: str | None = None,
        retryable: bool | None = None,
        status_code: int | None = None,
    ) -> None:
        """Publish an error event followed by a synthetic done event."""
        self._publish({
            "type": "error",
            "detail": detail,
            "reason": reason,
            "retryable": retryable,
            "status_code": status_code,
        })
        self._publish({
            "type": "done",
            "session_id": self.session_id,
            "reply": "",
            "trace": [],
            "skills_touched": [],
            "iterations": 0,
            "usage": {},
            "messages": None,
            "model": self.model_id,
        })

    def _persist(self) -> None:
        from ..routes.chat_stream_helpers import persist_stream_turn
        persist_stream_turn(
            store=self.store,
            session_id=self.session_id,
            final_messages=self.acc.final_messages,
            pre_turn_history=self.pre_turn_history,
            user_message=self.message,
            accumulated_text=self.acc.accumulated_text,
            accumulated_tools=self.acc.accumulated_tools,
            partial_status=self.acc.partial_status,
        )


# ── Module-level registry ──────────────────────────────────────────────

_running_turns: dict[str, ChatTurnRunner] = {}


def get_running_turn(session_id: str) -> ChatTurnRunner | None:
    """Return the active ChatTurnRunner for a session, if any."""
    runner = _running_turns.get(session_id)
    if runner is not None and runner.task is not None and not runner.task.done():
        return runner
    return None


def cancel_running_turn(session_id: str) -> bool:
    """Cancel the active turn for a session. Returns True if something was cancelled."""
    runner = _running_turns.get(session_id)
    if runner is not None:
        return runner.cancel()
    return False
