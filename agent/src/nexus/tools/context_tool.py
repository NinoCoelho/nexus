"""Agent-facing context-budget tools: ``context_status`` and ``fork_session``.

``fork_session`` used to be a stub. It assembled a keyword-matched summary and
returned ``instructions: "The backend should create a child session"`` — but
nothing in the backend handled that, so no session was ever created while the
system prompt advertised the tool and ``context_status`` recommended it at the
orange and red zones. The model would tell the user it had started a new
session that did not exist. It now really creates one.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..agent.context import CURRENT_CONTEXT_WINDOW, CURRENT_HISTORY, CURRENT_SESSION_ID
from ..agent.llm import ToolSpec
from ..agent.loop.overflow import DEFAULT_FALLBACK_WINDOW, estimate_tokens
from ..agent.loop.zones import classify_zone

log = logging.getLogger(__name__)

# Live SessionStore, injected at server startup (see server/app.py). Mirrors
# the heartbeat drivers' ``set_store_ref`` pattern: the tool handler is sync
# and module-level, so it can't take the store as an argument, and
# constructing a second SessionStore per call would open a duplicate SQLite
# connection and re-run schema migration.
_STORE_REF: Any = None


def set_session_store(store: Any) -> None:
    """Wire the live session store so ``fork_session`` can create sessions."""
    global _STORE_REF
    _STORE_REF = store


CONTEXT_STATUS_TOOL = ToolSpec(
    name="context_status",
    description=(
        "Check current context window usage. Returns estimated tokens, "
        "zone (green/yellow/orange/red), message counts, and recommendations. "
        "Use this before starting complex multi-step operations to avoid "
        "running out of context mid-task."
    ),
    parameters={"type": "object", "properties": {}},
)


def _current_session_id() -> str | None:
    try:
        return CURRENT_SESSION_ID.get() or None
    except LookupError:
        return None


def handle_context_status(args: dict[str, Any]) -> str:
    history = CURRENT_HISTORY.get([])
    context_window = CURRENT_CONTEXT_WINDOW.get(0)
    msgs = history
    est = estimate_tokens(msgs)
    effective_window = context_window if context_window > 0 else DEFAULT_FALLBACK_WINDOW
    zone = classify_zone(est, effective_window)
    tool_count = sum(
        1
        for m in msgs
        if getattr(m, "role", None) == "tool"
        or (isinstance(m, dict) and m.get("role") == "tool")
    )
    total_count = len(msgs)

    recommendations = {
        "green": "Context is healthy. Continue as normal.",
        "yellow": (
            "Context is filling up. The user can compact from the context "
            "indicator in the status bar. Consider using spawn_subagents for "
            "independent tasks, or vault_write to persist intermediate results."
        ),
        "orange": (
            "Context is running low. Inform the user that they can compact "
            "from the context indicator. Use fork_session to start a new "
            "phase, or spawn_subagents for remaining work."
        ),
        "red": (
            "Context is critically full. Older turns will be summarized or "
            "trimmed automatically to keep the chat working, which loses "
            "detail. Persist anything important with vault_write, then use "
            "fork_session or ask the user to start a new chat."
        ),
    }

    return json.dumps({
        "ok": True,
        "tokens_estimated": est,
        "context_window": context_window if context_window > 0 else effective_window,
        "context_window_source": "configured" if context_window > 0 else "fallback",
        "percentage_used": round(est / effective_window * 100, 1) if effective_window > 0 else 0,
        "zone": zone,
        "message_count": total_count,
        "tool_message_count": tool_count,
        "recommendation": recommendations[zone],
    }, ensure_ascii=False)


FORK_SESSION_TOOL = ToolSpec(
    name="fork_session",
    description=(
        "Create a new chat session that inherits a summary of this "
        "conversation, and return its real session id. Use this at natural "
        "task boundaries: a new feature, a new debugging target, a new phase "
        "of work, or when context is getting large. The new session starts "
        "with a structured summary of goals, decisions, entities and open "
        "TODOs instead of the full transcript. Tell the user the new chat "
        "exists and what it is called; this conversation stays intact."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Title for the new session (e.g. 'Phase 2: API Implementation')",
            },
            "summary_focus": {
                "type": "string",
                "description": "What to emphasize in the summary (e.g. 'the API design decisions', 'the remaining TODOs')",
            },
        },
        "required": ["title"],
    },
)


def _heuristic_summary(msgs: list[Any], summary_focus: str) -> str:
    """Keyword-extracted session memory.

    Fallback for when no LLM-generated session note exists yet (the session
    has never been compacted). Cheap and synchronous by design — the tool
    handler runs inside the tool-dispatch path and must not make an LLM call.
    """
    goals: list[str] = []
    decisions: list[str] = []
    entities: list[str] = []
    todos: list[str] = []

    for m in msgs:
        role = getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else None)
        content = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else None)
        if not isinstance(content, str) or not content:
            continue
        if role == "user":
            if any(
                kw in content.lower()
                for kw in ("implement", "create", "build", "fix", "add", "refactor", "write")
            ):
                goals.append(content[:200])
        elif role == "assistant":
            if any(
                kw in content.lower()
                for kw in ("decision:", "decided", "let's use", "we'll go with", "approach:")
            ):
                decisions.append(content[:200])
            for word in content.split():
                if word.endswith((".py", ".ts", ".tsx", ".js", ".json", ".md", ".toml")):
                    if word not in entities:
                        entities.append(word)
            if any(
                kw in content.lower()
                for kw in ("todo:", "remaining:", "still need", "next step")
            ):
                todos.append(content[:200])

    parts = ["## Session Memory (carried over from the previous chat)\n"]
    if goals:
        parts.append("- **Goals:**")
        parts.extend(f"  - {g}" for g in goals[-5:])
    if decisions:
        parts.append("- **Key Decisions:**")
        parts.extend(f"  - {d}" for d in decisions[-5:])
    if entities:
        parts.append("- **Entities (files):**")
        parts.extend(f"  - `{e}`" for e in entities[-15:])
    if todos:
        parts.append("- **Open TODOs:**")
        parts.extend(f"  - {t}" for t in todos[-5:])
    if summary_focus:
        parts.append(f"- **Focus area:** {summary_focus}")
    if len(parts) == 1:
        parts.append(
            "- **Last state:** forked from an earlier chat; no structured "
            "summary was available at fork time."
        )
    return "\n".join(parts)


def handle_fork_session(args: dict[str, Any]) -> str:
    title = (args.get("title") or "Continued session").strip() or "Continued session"
    summary_focus = args.get("summary_focus", "") or ""
    history = CURRENT_HISTORY.get([])

    parent_id = _current_session_id()
    store = _STORE_REF

    # Prefer the LLM-written rolling session note; it is far better than
    # keyword extraction and is already kept up to date by compaction.
    summary = ""
    if parent_id:
        try:
            from ..agent.loop.summarize import load_session_summary

            summary = load_session_summary(parent_id) or ""
        except Exception:  # noqa: BLE001 — fall back to the heuristic
            log.debug("fork_session: could not load session note", exc_info=True)
    if not summary:
        summary = _heuristic_summary(list(history), summary_focus)
    elif summary_focus:
        summary = f"{summary}\n\n- **Focus for this fork:** {summary_focus}"

    if store is None or not parent_id:
        # Report the failure instead of claiming success — the old stub always
        # returned ok:true, so the model told the user about a session that
        # was never created.
        return json.dumps({
            "ok": False,
            "error": (
                "fork_session is unavailable in this context (no live session "
                "store). Ask the user to start a new chat manually and paste "
                "the summary below into it."
            ),
            "summary": summary,
        }, ensure_ascii=False)

    try:
        from ..agent.llm import ChatMessage, Role
        from ..agent.loop.summarize import _SUMMARY_PREFIX, persist_session_summary

        parent = store.get(parent_id)
        child = store.create_child(
            parent_session_id=parent_id,
            title=title,
            hidden=False,
            context=summary,
            project_id=getattr(parent, "project_id", None),
        )
        seed = ChatMessage(
            role=Role.SYSTEM,
            content=(
                f"{_SUMMARY_PREFIX} — carried over from chat {parent_id}]\n{summary}"
            ),
        )
        store.replace_history(child.id, [seed])
        # Persist the note under the child id too, so a later resume reseeds
        # from disk even if this first message is ever cleared.
        persist_session_summary(child.id, summary)
    except Exception as exc:  # noqa: BLE001 — surfaced to the model
        log.exception("fork_session: failed to create child session")
        return json.dumps({
            "ok": False,
            "error": f"Could not create the new session: {exc}",
            "summary": summary,
        }, ensure_ascii=False)

    log.info("fork_session: created %s from %s (%r)", child.id, parent_id, title)
    return json.dumps({
        "ok": True,
        "session_id": child.id,
        "parent_session_id": parent_id,
        "title": title,
        "summary": summary,
        "source_message_count": len(history),
        "session_marker": f"nx:session={child.id}",
        "instructions": (
            "The new chat now exists and is seeded with the summary. Tell the "
            "user its title and that it appears in their chat list (inside the "
            "same project, if this chat belongs to one). Do not claim any work "
            "has been done in it yet."
        ),
    }, ensure_ascii=False)
