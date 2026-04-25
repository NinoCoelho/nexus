"""Background-turn helper for vault_dispatch.

Extracted from vault_dispatch.py to keep that module under 300 LOC.
The public entry point is :func:`run_background_agent_turn`; called
only from vault_dispatch._dispatch_impl.
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from ...agent.context import CURRENT_SESSION_ID, DISPATCH_CHAIN

if TYPE_CHECKING:
    from ...agent.loop import Agent
    from ..session_store import SessionStore

log = logging.getLogger(__name__)


async def run_background_agent_turn(
    *,
    session_id: str,
    seed_message: str,
    card_path: str,
    card_id: str,
    agent_: "Agent",
    store: "SessionStore",
    model_id: str | None = None,
) -> None:
    """Run one agent turn to completion, publishing events via the trace bus
    and updating the card's status (done/failed) when finished."""
    from ... import vault_kanban
    token = CURRENT_SESSION_ID.set(session_id)
    # Record this card on the dispatch chain so any move_card the agent
    # performs during this turn is recognised by the lane-change hook
    # as descended from this dispatch (cycle + depth guards).
    chain_token = DISPATCH_CHAIN.set(DISPATCH_CHAIN.get() + (card_id,))
    try:
        session = store.get_or_create(session_id)
        pre_turn = list(session.history)
        final_messages = None
        accumulated_text = ""
        accumulated_tools: list[dict[str, Any]] = []
        try:
            from ...agent.llm import ChatMessage as _CM, Role as _R
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
                model_id=model_id or None,
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
        DISPATCH_CHAIN.reset(chain_token)
