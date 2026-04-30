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

# Cap how many follow-up turns one card can chain through the same session
# when the agent keeps moving it to lanes with prompts. Matches the cascade
# limit used by the lane-change hook in app.py.
MAX_FOLLOW_UP_DEPTH = 5


async def run_background_agent_turn(
    *,
    session_id: str,
    seed_message: str,
    card_path: str,
    card_id: str,
    agent_: "Agent",
    store: "SessionStore",
    model_id: str | None = None,
    entity_kind: str = "card",
    occurrence_start: str | None = None,
) -> None:
    """Run one agent turn to completion, publishing events via the trace bus
    and updating the entity's status (done/failed) when finished.

    ``entity_kind`` selects which vault module owns the linked entity. Defaults
    to ``"card"`` (kanban) for back-compat; pass ``"event"`` to dispatch a
    calendar event, or ``"none"`` for ephemeral runs (e.g. dashboard
    operations) that have no on-disk entity to status-stamp.
    ``card_path``/``card_id`` are the entity's vault path and id regardless of
    kind (ignored when ``entity_kind="none"``).

    For kanban cards: when the agent moves the card during this turn into a
    *different* lane that has a prompt configured, a follow-up turn is run on
    the *same session* using that lane's prompt. This gives the agent's own
    moves the same auto-run behavior as a UI drag-drop, but without losing the
    conversation history. Bounded by ``MAX_FOLLOW_UP_DEPTH``.
    """
    starting_lane_id: str | None = None
    if entity_kind == "card":
        starting_lane_id = _read_card_lane(card_path, card_id)

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
            if entity_kind == "none":
                # Ephemeral run (e.g. a dashboard operation) — there's no
                # vault entity to flip status on. The session itself carries
                # the run state via its persisted history.
                pass
            elif entity_kind == "event":
                from ... import vault_calendar
                cal = vault_calendar.read_calendar(card_path)
                found = vault_calendar.find_event(cal, card_id)
                if found and found[0].has_fire_window:
                    # Fire-window events keep status="scheduled" so the next
                    # intra-day slot can fire them. Don't overwrite.
                    pass
                elif (
                    found
                    and found[0].rrule
                    and occurrence_start
                    and new_status == "done"
                ):
                    # Recurring event success: log the completed occurrence
                    # without flipping the parent's status. Mutating
                    # ``status`` would propagate "done" to every other
                    # expanded occurrence in the UI and re-trigger the
                    # original "marks all done" bug.
                    vault_calendar.update_event(
                        card_path, card_id,
                        {"complete_occurrence": occurrence_start},
                    )
                else:
                    vault_calendar.update_event(card_path, card_id, {"status": new_status})
            else:
                from ... import vault_kanban
                vault_kanban.update_card(card_path, card_id, {"status": new_status})
        except Exception:
            log.exception("background dispatch: %s status update failed", entity_kind)

        if entity_kind == "card" and starting_lane_id is not None:
            try:
                await _maybe_follow_up_after_move(
                    session_id=session_id,
                    card_path=card_path,
                    card_id=card_id,
                    starting_lane_id=starting_lane_id,
                    agent_=agent_,
                    store=store,
                )
            except Exception:
                log.exception("background dispatch: follow-up turn failed")
    finally:
        CURRENT_SESSION_ID.reset(token)
        DISPATCH_CHAIN.reset(chain_token)


def _read_card_lane(card_path: str, card_id: str) -> str | None:
    """Return the id of the lane currently holding this card, or None."""
    try:
        from ... import vault_kanban
        from ...vault_kanban.cards import _find_card

        board = vault_kanban.read_board(card_path)
        found = _find_card(board, card_id)
        return found[0].id if found else None
    except Exception:
        log.exception("follow-up: could not snapshot card lane")
        return None


async def _maybe_follow_up_after_move(
    *,
    session_id: str,
    card_path: str,
    card_id: str,
    starting_lane_id: str,
    agent_: "Agent",
    store: "SessionStore",
) -> None:
    """Run a follow-up turn on the same session when the card was moved into
    a different lane that has its own prompt.

    No-op if the card stayed put, was deleted, was moved to a lane without a
    prompt, or the dispatch chain has reached ``MAX_FOLLOW_UP_DEPTH``.
    """
    chain = DISPATCH_CHAIN.get()
    if len(chain) >= MAX_FOLLOW_UP_DEPTH:
        log.info(
            "follow-up: depth limit reached (%d) for card %s, chain=%s",
            MAX_FOLLOW_UP_DEPTH, card_id, chain,
        )
        return

    from ... import vault_kanban
    from ...vault_kanban.cards import _find_card

    board = vault_kanban.read_board(card_path)
    found = _find_card(board, card_id)
    if found is None:
        return  # card was deleted during the turn
    final_lane, card, _ = found
    if final_lane.id == starting_lane_id or not final_lane.prompt:
        return  # stayed put, or destination lane has no prompt

    # Lazy import to avoid circular: vault_dispatch imports from us.
    from .vault_dispatch import _compose_card_context_seed, _resolve_dispatch_model

    log.info(
        "follow-up: card %s moved %s -> %s during turn, running lane prompt on session %s",
        card_id, starting_lane_id, final_lane.id, session_id,
    )

    seed = _compose_card_context_seed(
        lane_prompt=final_lane.prompt,
        path=card_path,
        card_title=card.title,
        card_id=card.id,
        card_body=card.body,
        current_lane_id=final_lane.id,
        current_lane_title=final_lane.title,
        lanes=[(ln.id, ln.title) for ln in board.lanes],
    )
    # Mark running again before recursing — status was just set to done/failed.
    try:
        vault_kanban.update_card(card_path, card_id, {"status": "running"})
    except Exception:
        log.exception("follow-up: could not mark card running")

    follow_up_model = _resolve_dispatch_model(final_lane.model, agent_)
    await run_background_agent_turn(
        session_id=session_id,
        seed_message=seed,
        card_path=card_path,
        card_id=card_id,
        agent_=agent_,
        store=store,
        model_id=follow_up_model,
        entity_kind="card",
    )
