"""Background-turn helper for vault_dispatch.

Extracted from vault_dispatch.py to keep that module under 300 LOC.
The public entry point is :func:`run_background_agent_turn`; called
only from vault_dispatch._dispatch_impl.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ...agent.context import DISPATCH_CHAIN
from ..services.background_turn import run_background_turn

if TYPE_CHECKING:
    from ...agent.loop import Agent
    from ..session_store import SessionStore

log = logging.getLogger(__name__)

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

    chain_token = DISPATCH_CHAIN.set(DISPATCH_CHAIN.get() + (card_id,))
    try:
        result = await run_background_turn(
            session_id=session_id,
            seed_message=seed_message,
            agent_=agent_,
            store=store,
            model_id=model_id,
            partial_status_note="background_interrupted",
        )
        new_status = result.status

        try:
            if entity_kind == "none":
                pass
            elif entity_kind == "event":
                from ... import vault_calendar

                cal = vault_calendar.read_calendar(card_path)
                found = vault_calendar.find_event(cal, card_id)
                if found and found[0].has_fire_window:
                    pass
                elif found and found[0].rrule:
                    if new_status == "done" and occurrence_start:
                        vault_calendar.update_event(
                            card_path,
                            card_id,
                            {"complete_occurrence": occurrence_start},
                        )
                else:
                    vault_calendar.update_event(
                        card_path, card_id, {"status": new_status}
                    )
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
        DISPATCH_CHAIN.reset(chain_token)


def _read_card_lane(card_path: str, card_id: str) -> str | None:
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
    chain = DISPATCH_CHAIN.get()
    if len(chain) >= MAX_FOLLOW_UP_DEPTH:
        log.info(
            "follow-up: depth limit reached (%d) for card %s, chain=%s",
            MAX_FOLLOW_UP_DEPTH,
            card_id,
            chain,
        )
        return

    from ... import vault_kanban
    from ...vault_kanban.cards import _find_card

    board = vault_kanban.read_board(card_path)
    found = _find_card(board, card_id)
    if found is None:
        return
    final_lane, card, _ = found
    if final_lane.id == starting_lane_id or not final_lane.prompt:
        return

    from .vault_dispatch import _compose_card_context_seed, _resolve_dispatch_model

    log.info(
        "follow-up: card %s moved %s -> %s during turn, running lane prompt on session %s",
        card_id,
        starting_lane_id,
        final_lane.id,
        session_id,
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
