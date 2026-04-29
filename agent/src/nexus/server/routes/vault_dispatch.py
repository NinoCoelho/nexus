"""Routes and helpers for vault dispatch: POST /vault/dispatch.

Dispatch creates a new chat session seeded from a vault file or kanban card.
Helper functions _compose_card_context_seed, _compose_hidden_chat_seed,
_run_background_agent_turn, and _dispatch_impl are defined here and also
re-exported for the agent's dispatch_card tool (via app.state._agent_dispatcher).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from ..deps import get_agent, get_sessions
from ...agent.loop import Agent
from ..session_store import SessionStore

log = logging.getLogger(__name__)

router = APIRouter()


def _resolve_dispatch_model(requested: str | None, agent_: "Agent") -> str | None:
    """Validate the requested model id against the live provider registry,
    falling back to the agent's configured default when the request is
    missing or unavailable.

    Returns ``None`` when no usable model is known (caller lets the agent
    loop pick whatever default it has wired up).
    """
    if requested:
        requested = requested.strip() or None
    pr = getattr(agent_, "_provider_registry", None)
    available = pr.available_model_ids() if pr is not None else []
    if requested and (not available or requested in available):
        return requested
    cfg = getattr(agent_, "_nexus_cfg", None)
    default = (cfg.agent.default_model if cfg and getattr(cfg, "agent", None) else None) or None
    if requested:
        log.warning(
            "dispatch: requested model %r not in available %s; falling back",
            requested, available,
        )
    if default and (not available or default in available):
        return default
    if available:
        return available[0]
    return None

# Marker that the UI strips from displayed messages. The agent still
# sees the full content (it's persisted like any user message), but
# the chat bubble list filters messages starting with this sentinel.
HIDDEN_SEED_MARKER = "<!-- nx:hidden-seed -->\n"


def _compose_card_context_seed(
    *,
    lane_prompt: str | None,
    path: str,
    card_title: str,
    card_id: str,
    card_body: str,
    current_lane_id: str | None = None,
    current_lane_title: str | None = None,
    lanes: list[tuple[str, str]] | None = None,
) -> str:
    """Build the seed message for a background lane-prompt dispatch.

    ``lanes`` is the full ordered list of (lane_id, lane_title) on the
    board so the agent can resolve relative refs like "next lane" without
    having to read the board first. ``current_lane_*`` identifies which
    of those lanes contains the card right now.
    """
    folder = path.rsplit("/", 1)[0] if "/" in path else ""
    parts = []
    if lane_prompt:
        parts.append(lane_prompt.strip())
        parts.append("")
    parts.append(f"**Board:** `{path}`")
    if folder:
        parts.append(f"**Folder:** `{folder}/`")
    parts.append(f"**Card:** {card_title}")
    parts.append(f"**Card ID:** `{card_id}`")
    if current_lane_id:
        parts.append(
            f"**Current lane:** {current_lane_title or current_lane_id} "
            f"(id `{current_lane_id}`)"
        )
    if lanes:
        parts.append("**Lanes (in order):**")
        for lid, ltitle in lanes:
            marker = "  ← current" if lid == current_lane_id else ""
            parts.append(f"- `{lid}` — {ltitle}{marker}")
    parts.append("")
    if card_body.strip():
        parts.append(card_body.strip())
        parts.append("")
    parts.append(
        "*Tools: use `kanban_manage` to mutate this board — for example "
        f'`{{"action":"move_card","path":"{path}","card_id":"{card_id}","lane":"<lane_id>"}}` '
        "to move this card to another lane (use the lane ids above), or "
        "`update_card` to change title/body/status/labels/etc. Use your "
        "vault tools for related files (typically in the same folder).*"
    )
    return "\n".join(parts)


def _compose_hidden_chat_seed(*, path: str, card_title: str, card_id: str, card_body: str) -> str:
    folder = path.rsplit("/", 1)[0] if "/" in path else ""
    body = [
        HIDDEN_SEED_MARKER.rstrip(),
        f"The user just opened this kanban card. Check the board file at `{path}`, "
        f"read the card, suggest 2-3 concrete next steps to the user, and then wait for instructions. "
        f"Don't make changes yet.",
        "",
        f"**Board:** `{path}`",
    ]
    if folder:
        body.append(f"**Folder:** `{folder}/`")
    body.append(f"**Card:** {card_title}")
    body.append(f"**Card ID:** `{card_id}`")
    if card_body.strip():
        body.append("")
        body.append(card_body.strip())
    return "\n".join(body)


def _compose_event_context_seed(
    *,
    calendar_prompt: str | None,
    path: str,
    event_title: str,
    event_id: str,
    event_body: str,
    start: str,
    end: str | None,
    rrule: str | None,
) -> str:
    """Build the seed message for a background calendar-event dispatch."""
    folder = path.rsplit("/", 1)[0] if "/" in path else ""
    parts: list[str] = []
    if calendar_prompt:
        parts.append(calendar_prompt.strip())
        parts.append("")
    parts.append(f"**Calendar:** `{path}`")
    if folder:
        parts.append(f"**Folder:** `{folder}/`")
    parts.append(f"**Event:** {event_title}")
    parts.append(f"**Event ID:** `{event_id}`")
    parts.append(f"**Start:** {start}")
    if end:
        parts.append(f"**End:** {end}")
    if rrule:
        parts.append(f"**Recurrence (RRULE):** `{rrule}`")
    parts.append("")
    if event_body.strip():
        parts.append(event_body.strip())
        parts.append("")
    parts.append(
        "*Tools: use `calendar_manage` to mutate this calendar — for example "
        f'`{{"action":"update_event","path":"{path}","event_id":"{event_id}","status":"done"}}` '
        "to mark this event done, or `add_event` to create a follow-up. Use your "
        "vault tools for related files (typically in the same folder).*"
    )
    return "\n".join(parts)


def _compose_hidden_event_seed(
    *,
    path: str,
    event_title: str,
    event_id: str,
    event_body: str,
    start: str,
) -> str:
    folder = path.rsplit("/", 1)[0] if "/" in path else ""
    body = [
        HIDDEN_SEED_MARKER.rstrip(),
        f"The user just opened this calendar event. Check the calendar file at `{path}`, "
        f"read the event, suggest 2-3 concrete next steps to the user, and then wait for "
        f"instructions. Don't make changes yet.",
        "",
        f"**Calendar:** `{path}`",
    ]
    if folder:
        body.append(f"**Folder:** `{folder}/`")
    body.append(f"**Event:** {event_title}")
    body.append(f"**Event ID:** `{event_id}`")
    body.append(f"**Start:** {start}")
    if event_body.strip():
        body.append("")
        body.append(event_body.strip())
    return "\n".join(body)


async def _run_background_agent_turn(
    *,
    session_id: str,
    seed_message: str,
    card_path: str,
    card_id: str,
    agent_: Agent,
    store: SessionStore,
    model_id: str | None = None,
    entity_kind: str = "card",
    occurrence_start: str | None = None,
) -> None:
    """Run one agent turn to completion, publishing events via the trace bus
    and updating the entity's status (done/failed) when finished."""
    from .vault_dispatch_helpers import run_background_agent_turn
    await run_background_agent_turn(
        session_id=session_id,
        seed_message=seed_message,
        card_path=card_path,
        card_id=card_id,
        agent_=agent_,
        store=store,
        model_id=model_id,
        entity_kind=entity_kind,
        occurrence_start=occurrence_start,
    )


async def _dispatch_impl(
    *,
    path: str,
    card_id: str | None,
    mode: str,
    a: "Agent",
    store: "SessionStore",
    event_id: str | None = None,
    occurrence_start: str | None = None,
) -> dict:
    """Shared implementation for /vault/dispatch and the dispatch_card tool.

    Either ``card_id`` or ``event_id`` may be provided (mutually exclusive).
    A bare path with neither acts as a generic vault dispatch (chat mode only).

    Raises ValueError / FileNotFoundError / KeyError on user errors so callers
    can translate (HTTP route → HTTPException; tool → JSON error string).
    """
    from ... import vault, vault_kanban
    if mode not in ("chat", "background", "chat-hidden"):
        raise ValueError("invalid mode")
    if not path:
        raise ValueError("`path` required")
    if card_id and event_id:
        raise ValueError("pass at most one of `card_id` or `event_id`")
    try:
        file = vault.read_file(path)
    except FileNotFoundError:
        raise FileNotFoundError("file not found")

    title = path.rsplit("/", 1)[-1]
    seed_body = file.get("body") or file["content"]
    seed_title = title
    lane_prompt: str | None = None
    lane_model: str | None = None
    current_lane_id: str | None = None
    current_lane_title: str | None = None
    all_lanes: list[tuple[str, str]] = []
    # calendar branch state
    calendar_prompt: str | None = None
    event_start: str = ""
    event_end: str | None = None
    event_rrule: str | None = None
    entity_kind = "card"

    if card_id:
        try:
            board = vault_kanban.parse(file["content"])
        except Exception as exc:
            raise ValueError(str(exc))
        from ...vault_kanban.cards import _find_card
        found = _find_card(board, card_id)
        if found is None:
            raise KeyError("card not found")
        lane, card, _ = found
        seed_title = card.title
        seed_body = card.body
        lane_prompt = lane.prompt
        lane_model = lane.model
        current_lane_id = lane.id
        current_lane_title = lane.title
        all_lanes = [(ln.id, ln.title) for ln in board.lanes]

    is_fire_window_event = False
    if event_id:
        from ... import vault_calendar
        try:
            cal = vault_calendar.parse(file["content"])
        except Exception as exc:
            raise ValueError(str(exc))
        found_ev = vault_calendar.find_event(cal, event_id)
        if found_ev is None:
            raise KeyError("event not found")
        ev, _ = found_ev
        seed_title = ev.title
        seed_body = ev.body
        calendar_prompt = cal.calendar_prompt
        event_start = ev.start
        event_end = ev.end
        event_rrule = ev.rrule
        entity_kind = "event"
        is_fire_window_event = ev.has_fire_window
        lane_model = vault_calendar.effective_model(ev, cal)

    if mode == "background" and not (card_id or event_id):
        raise ValueError("background dispatch requires a card_id or event_id")

    context_str = f"Dispatched from vault file: {path}"
    if card_id:
        context_str += f" (card {card_id})"
    elif event_id:
        context_str += f" (event {event_id})"
    session = store.create(context=context_str)

    try:
        store.rename(session.id, (seed_title or title).strip()[:60])
    except Exception:
        log.exception("dispatch: title rename failed")

    if mode == "chat":
        seed_message = (
            f"# {seed_title}\n\n{seed_body}".strip()
            if seed_body
            else f"# {seed_title}"
        )
    elif mode == "chat-hidden":
        if event_id:
            seed_message = _compose_hidden_event_seed(
                path=path, event_title=seed_title, event_id=event_id,
                event_body=seed_body or "", start=event_start,
            )
        else:
            seed_message = _compose_hidden_chat_seed(
                path=path, card_title=seed_title, card_id=card_id or "",
                card_body=seed_body or "",
            )
    else:  # background
        if event_id:
            seed_message = _compose_event_context_seed(
                calendar_prompt=calendar_prompt, path=path,
                event_title=seed_title, event_id=event_id,
                event_body=seed_body or "", start=event_start,
                end=event_end, rrule=event_rrule,
            )
        else:
            seed_message = _compose_card_context_seed(
                lane_prompt=lane_prompt, path=path, card_title=seed_title,
                card_id=card_id or "", card_body=seed_body or "",
                current_lane_id=current_lane_id,
                current_lane_title=current_lane_title,
                lanes=all_lanes,
            )

    if card_id:
        updates: dict[str, Any] = {"session_id": session.id}
        if mode == "background":
            updates["status"] = "running"
        try:
            vault_kanban.update_card(path, card_id, updates)
        except Exception:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "dispatch: could not link session to card", exc_info=True,
            )
    elif event_id:
        from ... import vault_calendar
        ev_updates: dict[str, Any] = {"session_id": session.id}
        # Fire-window events stay "scheduled" forever — next intra-day slot
        # will fire them again. Don't overwrite status here.
        # Recurring events also stay "scheduled" because per-occurrence
        # completion is tracked in ``completed_occurrences`` (set by the
        # background turn helper on success); flipping the parent's status
        # to "triggered" would propagate through every expanded occurrence
        # in the UI.
        if (
            mode == "background"
            and not is_fire_window_event
            and not event_rrule
        ):
            ev_updates["status"] = "triggered"
        try:
            vault_calendar.update_event(path, event_id, ev_updates)
        except Exception:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "dispatch: could not link session to event", exc_info=True,
            )

    if mode == "background":
        resolved_model = _resolve_dispatch_model(lane_model, a)
        asyncio.create_task(
            _run_background_agent_turn(
                session_id=session.id,
                seed_message=seed_message,
                card_path=path,
                card_id=event_id or card_id,
                agent_=a,
                store=store,
                model_id=resolved_model,
                entity_kind=entity_kind,
                occurrence_start=occurrence_start if entity_kind == "event" else None,
            )
        )
        return {
            "session_id": session.id, "path": path, "card_id": card_id,
            "event_id": event_id, "mode": mode, "model": resolved_model,
        }

    return {
        "session_id": session.id,
        "seed_message": seed_message,
        "path": path,
        "card_id": card_id,
        "event_id": event_id,
        "mode": mode,
    }


@router.post("/vault/dispatch", status_code=status.HTTP_201_CREATED)
async def vault_dispatch(
    body: dict,
    a: Agent = Depends(get_agent),
    store: SessionStore = Depends(get_sessions),
) -> dict:
    """Create a chat session seeded from a vault file or kanban card.

    Body: ``{path, card_id?, mode?}`` where ``mode`` is one of:
      - ``"chat"`` (default): returns a seed the UI prefills into its input.
      - ``"background"``: starts the agent server-side; UI doesn't navigate.
        Stamps ``status=running`` on the card and updates to ``done``/``failed``
        when the turn finishes. Requires ``card_id``.
      - ``"chat-hidden"``: creates a session, seeds with a hidden user message,
        kicks off no background work — the UI will POST to ``/chat/stream`` itself
        with the returned ``seed_message``, which embeds a marker the chat view
        filters out of the displayed message list.
    """
    path = body.get("path", "")
    card_id = body.get("card_id")
    event_id = body.get("event_id")
    mode = body.get("mode") or "chat"
    try:
        return await _dispatch_impl(
            path=path, card_id=card_id, event_id=event_id,
            mode=mode, a=a, store=store,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
