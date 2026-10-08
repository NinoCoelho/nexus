"""Shared background agent-turn runner.

Encapsulates the common pattern shared by vault dispatch, skill wizard
builds, and dashboard operations:

1. Set ``CURRENT_SESSION_ID`` context var
2. Load / create session, snapshot pre-turn history
3. Persist pre-turn user message
4. Stream agent turn, accumulating text + tool calls
5. On ``done``: capture final messages, bump usage
6. On crash: mark failed
7. Persist final history or partial-turn record
8. Reset context var
9. Return a ``TurnResult`` summary

Also provides :func:`publish_terminal_event`, a small helper that inspects
a ``TurnResult`` and publishes a ``SessionEvent`` (default kind
``"op_done"``) so callers don't have to repeat the outcome→event dance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ...agent.context import CURRENT_SESSION_ID

if TYPE_CHECKING:
    from ...agent.loop import Agent
    from ..session_store import SessionStore

log = logging.getLogger(__name__)


@dataclass
class TurnResult:
    status: str
    final_messages: list[Any] | None = None
    accumulated_text: str = ""
    accumulated_tools: list[dict[str, Any]] = field(default_factory=list)
    # Provider-reported usage for the turn (input/output tokens, iterations,
    # cache hits). Exposed so recurring background work can report its own
    # cost — the coordinator sweep had no token visibility at all.
    usage: dict[str, Any] = field(default_factory=dict)

    def final_reply(self) -> str:
        """The last assistant message, falling back to the streamed text.

        ``accumulated_text`` concatenates deltas from *every* loop iteration,
        so on a multi-step turn it includes intermediate narration ("Let me
        check the projects...") ahead of the real answer. Callers that publish
        the reply want only the final message.
        """
        for msg in reversed(self.final_messages or []):
            role = getattr(getattr(msg, "role", None), "value", None) or getattr(
                msg, "role", None
            )
            if str(role).lower() != "assistant":
                continue
            content = getattr(msg, "content", None)
            if isinstance(content, str) and content.strip():
                return content.strip()
        return self.accumulated_text.strip()


async def run_background_turn(
    *,
    session_id: str,
    seed_message: str,
    agent_: Agent,
    store: SessionStore,
    model_id: str | None = None,
    partial_status_note: str = "background_interrupted",
    history_override: list[Any] | None = None,
    ephemeral: bool = False,
) -> TurnResult:
    """Run one agent turn outside a request, streaming into a ``TurnResult``.

    ``history_override`` replaces the session's stored history for this turn
    (pass ``[]`` to run on a fresh context). ``ephemeral=True`` additionally
    skips every write back to the session: the seed message, the final
    history, and the partial-turn fallback.

    Both default to the historical behaviour, so the vault-dispatch, skill-
    wizard and dashboard callers are unaffected. They exist for the
    coordinator sweep, which is a recurring read-only digest: persisting its
    transcript meant each sweep re-sent every previous sweep's prompt, tool
    calls and tool results, so the cost grew with the number of sweeps ever
    run. Usage is still recorded in both modes, so the cost stays visible.
    """
    token = CURRENT_SESSION_ID.set(session_id)
    try:
        session = store.get_or_create(session_id)
        pre_turn = list(session.history)
        turn_history = pre_turn if history_override is None else list(history_override)
        if not ephemeral:
            try:
                from ...agent.llm import ChatMessage as _CM, Role as _R

                store.replace_history(
                    session_id,
                    pre_turn + [_CM(role=_R.USER, content=seed_message)],
                )
            except Exception:
                log.exception("background turn: pre-turn persist failed")

        result = TurnResult(status="done")
        try:
            async for event in agent_.run_turn_stream(
                seed_message,
                history=turn_history,
                context=session.context,
                session_id=session_id,
                model_id=model_id or None,
            ):
                etype = event.get("type")
                if etype == "delta":
                    result.accumulated_text += event.get("text", "")
                elif etype in ("tool_exec_start", "tool_exec_result"):
                    if etype == "tool_exec_start":
                        result.accumulated_tools.append({
                            "name": event.get("name", ""),
                            "args": event.get("args"),
                            "status": "pending",
                        })
                    else:
                        for t in reversed(result.accumulated_tools):
                            if (
                                t.get("name") == event.get("name")
                                and t.get("status") == "pending"
                            ):
                                t["status"] = "done"
                                t["result_preview"] = event.get("result_preview")
                                break
                elif etype == "done":
                    result.final_messages = event.get("messages")
                    usage = event.get("usage") or {}
                    result.usage = {
                        **usage,
                        "iterations": event.get("iterations", 0),
                    }
                    try:
                        store.bump_usage(
                            session_id,
                            model=usage.get("model"),
                            input_tokens=int(usage.get("input_tokens") or 0),
                            output_tokens=int(usage.get("output_tokens") or 0),
                            tool_calls=int(usage.get("tool_calls") or 0),
                        )
                    except Exception:
                        log.exception("background turn: bump_usage failed")
            result.status = "done" if result.final_messages is not None else "failed"
        except Exception:
            log.exception("background turn: agent loop crashed")
            result.status = "failed"
        finally:
            if ephemeral:
                pass  # nothing is written back — see the docstring
            elif result.final_messages is not None:
                try:
                    store.replace_history(session_id, result.final_messages)
                except Exception:
                    log.exception("background turn: final persist failed")
            else:
                try:
                    store.persist_partial_turn(
                        session_id,
                        base_history=pre_turn,
                        user_message=seed_message,
                        assistant_text=result.accumulated_text,
                        tool_calls=result.accumulated_tools,
                        status_note=partial_status_note,
                    )
                except Exception:
                    log.exception("background turn: partial persist failed")
        return result
    finally:
        CURRENT_SESSION_ID.reset(token)


async def publish_terminal_event(
    *,
    session_id: str,
    result: TurnResult,
    store: SessionStore,
    event_kind: str = "op_done",
) -> dict:
    from ..events import SessionEvent

    error_msg: str | None = None
    if result.status == "failed":
        error_msg = result.accumulated_text[:200] or "Action did not complete."
    try:
        store.publish(
            session_id,
            SessionEvent(
                kind=event_kind,
                data={"status": result.status, "error": error_msg},
            ),
        )
    except Exception:
        log.exception("terminal event publish failed")
    return {"status": result.status, "error": error_msg}
