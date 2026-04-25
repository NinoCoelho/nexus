"""Persistence helpers for chat_stream.py.

Extracted from chat_stream.py to keep that module under 300 LOC.
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..session_store import SessionStore

log = logging.getLogger(__name__)


def persist_stream_turn(
    *,
    store: "SessionStore",
    session_id: str,
    final_messages: list | None,
    pre_turn_history: list,
    user_message: str,
    accumulated_text: str,
    accumulated_tools: list[dict[str, Any]],
    partial_status: str,
) -> None:
    """Persist the outcome of a streaming turn, handling all four cases:
    - Normal completion (replace history).
    - Truncated/empty/timeout completion with a message list (status-stamped partial).
    - Abnormal exit without a message list (partial persist from accumulated state).
    """
    if final_messages is not None and partial_status in (
        "length", "empty_response", "upstream_timeout",
    ):
        # Loom still delivered a final message list, but the turn was
        # truncated / empty / timed out. Stamp the status prefix onto
        # the persisted assistant so the UI renders a Retry/Continue
        # banner on reload. Falls back to the partial-turn writer which
        # knows how to prefix content.
        try:
            # Find the trailing assistant message and use its text +
            # tool_calls as the partial state.
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
                session_id,
                base_history=pre_turn_history,
                user_message=user_message,
                assistant_text=last_asst_text,
                tool_calls=last_asst_tools,
                status_note=partial_status,
            )
        except Exception:  # noqa: BLE001 — best-effort
            log.exception("status-stamped partial persist failed")
            store.replace_history(session_id, final_messages)
    elif final_messages is not None:
        store.replace_history(session_id, final_messages)
    else:
        # Stream didn't reach a `done` event — persist whatever we
        # accumulated so a reload can see the partial reply and the
        # tool badges that were already executed. This is what makes
        # the UI recover gracefully after a server restart, a cancel,
        # an LLM timeout, or a loop limit hit.
        try:
            store.persist_partial_turn(
                session_id,
                base_history=pre_turn_history,
                user_message=user_message,
                assistant_text=accumulated_text,
                tool_calls=accumulated_tools,
                status_note=partial_status,
            )
        except Exception:  # noqa: BLE001 — best-effort
            log.exception("partial turn persist failed")


def log_stream_trajectory(
    *,
    trajectory_logger: Any,
    session_id: str,
    turn_index: int,
    user_message: str,
    history_length: int,
    context: str,
    reply_text: str,
    model: str,
    iterations: int,
    input_tokens: int,
    output_tokens: int,
    tool_calls: int,
) -> None:
    """Best-effort trajectory log for a streaming turn — errors are swallowed."""
    try:
        trajectory_logger.log(
            session_id=session_id,
            turn_index=turn_index,
            state={
                "user_message": user_message,
                "history_length": history_length,
                "context": (context or "")[:200],
            },
            action={
                "reply": reply_text[:2000] if reply_text else "",
                "model": model or "",
                "iterations": iterations,
                "tool_calls": [],
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
            reward={
                "explicit": None,
                "implicit": {
                    "turn_completed": True,
                    "tool_call_count": tool_calls,
                },
            },
        )
    except Exception:  # noqa: BLE001 — best-effort
        log.exception("trajectory logging failed (stream)")
