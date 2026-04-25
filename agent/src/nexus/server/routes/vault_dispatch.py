"""Routes and helpers for vault dispatch: POST /vault/dispatch.

Dispatch creates a new chat session seeded from a vault file or kanban card.
Helper functions _compose_card_context_seed, _compose_hidden_chat_seed,
_run_background_agent_turn, and _dispatch_impl are defined here and also
re-exported for the agent's dispatch_card tool (via app.state._agent_dispatcher).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, status

from ..deps import get_agent, get_sessions
from ...agent.context import CURRENT_SESSION_ID, DISPATCH_CHAIN
from ...agent.loop import Agent
from ..session_store import SessionStore

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

router = APIRouter()

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


async def _run_background_agent_turn(
    *,
    session_id: str,
    seed_message: str,
    card_path: str,
    card_id: str,
    agent_: Agent,
    store: SessionStore,
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


async def _dispatch_impl(
    *,
    path: str,
    card_id: str | None,
    mode: str,
    a: "Agent",
    store: "SessionStore",
) -> dict:
    """Shared implementation for /vault/dispatch and the dispatch_card tool.

    Raises ValueError / FileNotFoundError / KeyError on user errors so callers
    can translate (HTTP route → HTTPException; tool → JSON error string).
    """
    from ... import vault, vault_kanban
    if mode not in ("chat", "background", "chat-hidden"):
        raise ValueError("invalid mode")
    if not path:
        raise ValueError("`path` required")
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

    if card_id:
        try:
            board = vault_kanban.parse(file["content"])
        except Exception as exc:
            raise ValueError(str(exc))
        found = vault_kanban._find_card(board, card_id)
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

    if mode == "background" and not card_id:
        raise ValueError("background dispatch requires a card_id")

    context_str = f"Dispatched from vault file: {path}"
    if card_id:
        context_str += f" (card {card_id})"
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
        seed_message = _compose_hidden_chat_seed(
            path=path, card_title=seed_title, card_id=card_id or "", card_body=seed_body or "",
        )
    else:  # background
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

    if mode == "background":
        asyncio.create_task(
            _run_background_agent_turn(
                session_id=session.id,
                seed_message=seed_message,
                card_path=path,
                card_id=card_id,
                agent_=a,
                store=store,
                model_id=lane_model,
            )
        )
        return {
            "session_id": session.id, "path": path, "card_id": card_id,
            "mode": mode, "model": lane_model,
        }

    return {
        "session_id": session.id,
        "seed_message": seed_message,
        "path": path,
        "card_id": card_id,
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
    mode = body.get("mode") or "chat"
    try:
        return await _dispatch_impl(path=path, card_id=card_id, mode=mode, a=a, store=store)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
