"""Helpers for chat.py: planner-mode turn execution and trajectory logging.

Extracted from chat.py to keep that module under 300 LOC.
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ...agent.loop import Agent, AgentTurn
    from ..session_store import SessionStore

log = logging.getLogger(__name__)


async def run_planner_turn(
    *,
    agent: "Agent",
    message: str,
    session: Any,
    cfg: Any,
    store: "SessionStore",
    publish_event_fn: Any,
) -> "AgentTurn":
    """Execute one turn via PlannerAgent and return a synthetic AgentTurn."""
    from ...agent.planner import PlannerAgent
    from ...agent.loop import AgentTurn
    from ...agent.llm import ChatMessage, Role
    from ..events import SessionEvent
    from ...agent.context import CURRENT_SESSION_ID

    trace_events: list[dict[str, Any]] = []

    def _on_planner_trace(event: dict[str, Any]) -> None:
        trace_events.append(event)
        # Also forward into the SSE channel so subscribers see plan events
        sid = CURRENT_SESSION_ID.get()
        if sid:
            store.publish(
                sid,
                SessionEvent(
                    kind=event.get("type", "plan_event"),
                    data={k: v for k, v in event.items() if k != "type"},
                ),
            )

    default_model = cfg.agent.default_model if cfg and cfg.agent else None
    provider, _ = agent._resolve_provider(default_model)
    planner = PlannerAgent(
        executor=agent,
        llm=provider,
        planner_model=None,
        on_trace=_on_planner_trace,
    )
    result = await planner.run_turn(
        message,
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
    extra_msg = ChatMessage(role=Role.ASSISTANT, content=reply_text)
    turn_messages = list(session.history) + [
        ChatMessage(role=Role.USER, content=message),
        extra_msg,
    ]
    return AgentTurn(
        reply=reply_text,
        skills_touched=[],
        iterations=1,
        trace=trace_events,
        messages=turn_messages,
        input_tokens=0,
        output_tokens=0,
        tool_calls=0,
        model=default_model,
    ), plan_data


def log_trajectory(
    *,
    trajectory_logger: Any,
    session_id: str,
    turn_index: int,
    user_message: str,
    history_length: int,
    context: str,
    reply: str,
    model: str,
    iterations: int,
    input_tokens: int,
    output_tokens: int,
    tool_calls: int,
) -> None:
    """Best-effort trajectory log — errors are swallowed."""
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
                "reply": reply[:2000] if reply else "",
                "model": model if model else "",
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
        log.exception("trajectory logging failed")
