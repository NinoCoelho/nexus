"""Persistence helpers for chat_stream.py.

Extracted from chat_stream.py to keep that module under 300 LOC.
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..session_store import SessionStore
    from ...agent.loop import Agent

log = logging.getLogger(__name__)


_FALLBACK_TITLE_PREFIXES = ("New session", "Sub-agent")
_TITLE_MAX_WORDS = 5
_TITLE_PROMPT = (
    "Summarize the user's request below as a short chat title. "
    "Strict rules: at most 5 words, Title Case, no quotes, no trailing "
    "punctuation, no preamble. Reply with ONLY the title.\n\n"
    "User request:\n"
)


async def maybe_autotitle_via_llm(
    *,
    store: "SessionStore",
    agent: "Agent",
    session_id: str,
    user_message: str,
) -> None:
    """Replace the placeholder title with an LLM-generated short summary.

    Best-effort: bails silently on provider errors, missing models, or empty
    output. Skips when the session already has a meaningful title (i.e. it's
    not the bootstrap "New session" / 40-char fallback). Designed to be
    invoked as a background task so it doesn't delay the streaming response.
    """
    from ...agent.llm import ChatMessage, Role

    try:
        snippet = (user_message or "").strip()
        if not snippet:
            return
        # Don't overwrite a user-set title.
        row = store._loom._db.execute(
            "SELECT title FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            return
        current = (row[0] or "").strip()
        first_msg_fallback = snippet[:40]
        if current and current not in _FALLBACK_TITLE_PREFIXES and current != first_msg_fallback:
            return

        # Resolve a *concrete* upstream model name. The agent's
        # `_nexus_provider` is built with `model=""` because the registry
        # tracks model names separately, so passing `model=None` to
        # provider.chat() would raise "No model specified". Pull the
        # configured default and resolve it through the registry to get the
        # real upstream id (e.g. "claude-haiku-4-5-20251001").
        cfg = getattr(agent, "_nexus_cfg", None)
        default_model_id = (
            getattr(getattr(cfg, "agent", None), "default_model", None) or ""
        )
        provider, upstream_model = agent._resolve_provider(default_model_id)
        if provider is None:
            return
        prompt = _TITLE_PROMPT + snippet[:300]
        messages = [ChatMessage(role=Role.USER, content=prompt)]
        try:
            resp = await provider.chat(messages, model=upstream_model, max_tokens=32)
        except Exception:  # noqa: BLE001 — best-effort, never break the turn
            log.warning("autotitle LLM call failed", exc_info=True)
            return
        raw = (resp.content or "").strip()
        if not raw:
            return
        # Single-line, strip surrounding quotes / trailing punctuation, then
        # enforce the 5-word cap ourselves — providers don't always honour it.
        first_line = raw.splitlines()[0] if raw else ""
        title = first_line.strip().strip('"').strip("'").rstrip(".!?").strip()
        if not title:
            return
        words = title.split()
        if len(words) > _TITLE_MAX_WORDS:
            title = " ".join(words[:_TITLE_MAX_WORDS])
        if len(title) > 60:
            title = title[:60].rstrip()
        store.rename(session_id, title)
    except Exception:  # noqa: BLE001 — never let titling crash the request
        log.exception("autotitle via LLM failed")


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
