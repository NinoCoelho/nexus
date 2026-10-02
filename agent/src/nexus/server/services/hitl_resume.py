"""Shared parked-HITL resume service.

Both ``POST /chat/{sid}/hitl/{rid}/answer`` (web SSE) and the Telegram
gateway need to (1) decode and atomically record a parked-request
answer, (2) drive ``Agent.continue_after_hitl``, (3) persist the
outcome, and (4) publish terminal events on the session bus so every
subscribed surface (web SSE channels, the Telegram reply streamer)
finalizes. This module owns that pipeline so the two answer front-ends
stay in lockstep.

Two-step API:

* :func:`prepare_parked_resume` — validate + mark the parked row
  answered. Returns ``None`` when no such row exists (the HTTP route
  maps that to 404) and ``duplicate=True`` when another client already
  answered (idempotent replay).
* :func:`drive_parked_resume` — async generator yielding the resumed
  turn's stream events. Handles the ``CURRENT_SESSION_ID`` contextvar
  (so the ``_trace`` hook fans deltas onto the session bus), optional
  task registration for cancellation, persistence via
  ``persist_stream_turn``, usage bumping, and a synthetic
  ``turn_settled`` bus event — the Telegram streamer's finalize marker,
  which otherwise only ``ChatTurnRunner`` publishes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from ..events import SessionEvent
from ..session_store import SessionStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedResume:
    """A parked request whose answer has been recorded (or was already)."""

    session_id: str
    request_id: str
    decoded: Any
    # Answer as persisted on the (possibly already-answered) row — the
    # duplicate path returns it to the client verbatim.
    answer_json: str | None
    duplicate: bool


async def prepare_parked_resume(
    store: SessionStore,
    *,
    session_id: str,
    request_id: str,
    raw_answer: Any,
) -> PreparedResume | None:
    """Validate ownership and atomically mark the parked row answered.

    Returns ``None`` when no parked row exists. Raises ``ValueError``
    when the row belongs to a different session. Decodes JSON-string
    answers the same way ``ask_user_tool`` decodes broker answers
    (form payloads arrive as JSON strings; everything else stays text).
    """
    row = store.get_hitl_pending(request_id)
    if row is None:
        return None
    if row.get("session_id") != session_id:
        raise ValueError(
            f"request {request_id!r} belongs to session "
            f"{row.get('session_id')!r}, not {session_id!r}"
        )

    decoded: Any = raw_answer
    if isinstance(raw_answer, str):
        try:
            decoded = json.loads(raw_answer)
        except (json.JSONDecodeError, ValueError):
            decoded = raw_answer

    answered = store.mark_hitl_pending_answered(request_id, decoded)
    if answered is None:
        return None
    return PreparedResume(
        session_id=session_id,
        request_id=request_id,
        decoded=decoded,
        answer_json=answered.get("answer_json"),
        duplicate=bool(answered.get("already_answered")),
    )


def _publish_bus_error(store: SessionStore, session_id: str, detail: str) -> None:
    """Fan a terminal error onto the session bus (Telegram renders ⚠️)."""
    try:
        store.publish(
            session_id,
            SessionEvent(
                kind="error",
                data={"type": "error", "detail": detail},
            ),
        )
    except Exception:  # noqa: BLE001
        log.exception("hitl resume: bus error publish failed")


async def drive_parked_resume(
    agent: Any,
    store: SessionStore,
    prepared: PreparedResume,
    *,
    task_registry: dict[str, asyncio.Task[Any]] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Drive ``Agent.continue_after_hitl`` with all side effects.

    Yields the resumed turn's stream events (deltas, tool calls, done,
    synthetic error events on failure). Deltas reach the session bus
    via the ``_trace`` hook — this generator sets the contextvar — so
    bus subscribers (Telegram streamer, web out-of-band channel) see
    them without any consumer attached here.

    ``task_registry`` maps session_id → current task while the resume
    runs so cancellation endpoints (``/chat/{sid}/cancel``) can abort
    it; pass the route's ``_inflight_turns`` dict or a private one.
    """
    from ...agent.context import CURRENT_SESSION_ID
    from ...agent.llm import LLMTransportError, MalformedOutputError
    from ..routes._streaming import TurnAccumulator
    from ..routes.chat_stream_helpers import persist_stream_turn

    session_id = prepared.session_id
    token = CURRENT_SESSION_ID.set(session_id)
    current = asyncio.current_task()
    if task_registry is not None and current is not None:
        task_registry[session_id] = current

    # Accumulate state for persistence. process_event's SSE frames are
    # discarded here — HTTP consumers run their own accumulator over the
    # yielded events; this one exists purely for the persist step.
    acc = TurnAccumulator()
    pre_turn_history: list[Any] = []
    sess = store.get(session_id)
    if sess is not None:
        pre_turn_history = list(sess.history)

    try:
        try:
            async for event in agent.continue_after_hitl(
                session_id=session_id,
                request_id=prepared.request_id,
                answer=prepared.decoded,
            ):
                etype = event.get("type")
                if etype == "done":
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
                        log.exception("bump_usage failed (hitl resume)")
                acc.process_event(event)
                yield event
        except (LLMTransportError, MalformedOutputError) as exc:
            acc.partial_status = "llm_error"
            yield {"type": "error", "detail": str(exc)}
            _publish_bus_error(store, session_id, str(exc))
        except asyncio.CancelledError:
            acc.partial_status = "cancelled"
            yield {
                "type": "error",
                "detail": "cancelled by user",
                "reason": "cancelled",
            }
            yield {
                "type": "done",
                "session_id": session_id,
                "reply": "",
                "trace": [],
                "skills_touched": [],
                "iterations": 0,
                "usage": {},
                "messages": None,
            }
            _publish_bus_error(store, session_id, "cancelled by user")
            raise
        except Exception as exc:  # noqa: BLE001
            acc.partial_status = "crashed"
            log.exception("hitl resume crashed")
            detail = f"{type(exc).__name__}: {exc}"
            yield {"type": "error", "detail": detail}
            yield {
                "type": "done",
                "session_id": session_id,
                "reply": "",
                "trace": [],
                "skills_touched": [],
                "iterations": 0,
                "usage": {},
                "messages": None,
            }
            _publish_bus_error(store, session_id, detail)
    finally:
        try:
            persist_stream_turn(
                store=store,
                session_id=session_id,
                final_messages=acc.final_messages,
                pre_turn_history=pre_turn_history,
                user_message="",
                accumulated_text=acc.accumulated_text,
                accumulated_tools=acc.accumulated_tools,
                partial_status=acc.partial_status,
            )
        except Exception:  # noqa: BLE001
            log.exception("persist (hitl resume) failed")
        try:
            CURRENT_SESSION_ID.reset(token)
        except ValueError:
            log.debug("CURRENT_SESSION_ID reset across contexts (hitl resume)")
        if task_registry is not None and task_registry.get(session_id) is current:
            task_registry.pop(session_id, None)
        # Terminal marker for bus subscribers — the Telegram streamer
        # finalizes its message (and upgrades acks) on turn_settled,
        # which only ChatTurnRunner publishes otherwise. Suppressed when
        # a fresh ChatTurnRunner owns the session (user answered a
        # parked prompt while already running a new message): that
        # runner's own turn_settled closes its SSE stream, and a foreign
        # marker would close it prematurely.
        try:
            from .chat_turn_runner import get_running_turn

            suppress_marker = get_running_turn(session_id) is not None
        except Exception:  # noqa: BLE001 — defensive import
            suppress_marker = False
        if suppress_marker:
            log.debug(
                "hitl resume: turn_settled suppressed — ChatTurnRunner active on %s",
                session_id[:8],
            )
            return
        try:
            store.publish(
                session_id,
                SessionEvent(
                    kind="turn_settled",
                    data={"type": "turn_settled", "session_id": session_id},
                ),
            )
        except Exception:  # noqa: BLE001
            log.exception("hitl resume: turn_settled publish failed")
