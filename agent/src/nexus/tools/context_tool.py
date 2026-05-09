from __future__ import annotations

import json
from typing import Any

from ..agent.context import CURRENT_HISTORY, CURRENT_CONTEXT_WINDOW
from ..agent.llm import ToolSpec
from ..agent.loop.overflow import estimate_tokens, _DEFAULT_FALLBACK_WINDOW
from ..agent.loop.zones import classify_zone

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


def handle_context_status(args: dict[str, Any]) -> str:
    history = CURRENT_HISTORY.get([])
    context_window = CURRENT_CONTEXT_WINDOW.get(0)
    msgs = history
    est = estimate_tokens(msgs)
    effective_window = context_window if context_window > 0 else _DEFAULT_FALLBACK_WINDOW
    zone = classify_zone(est, effective_window)
    tool_count = sum(1 for m in msgs if getattr(m, "role", None) == "tool" or (isinstance(m, dict) and m.get("role") == "tool"))
    total_count = len(msgs)

    recommendations = {
        "green": "Context is healthy. Continue as normal.",
        "yellow": "Context is filling up. The user can compact from the context indicator in the status bar. Consider using spawn_subagents for independent tasks, or vault_write to persist intermediate results.",
        "orange": "Context is running low. Inform the user that they can compact from the context indicator. Use fork_session to start a new phase, or spawn_subagents for remaining work.",
        "red": "Context is critically full. Inform the user they should compact or start a new session. Risk of empty responses or truncation.",
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
        "Create a new session with a summary of the current conversation. "
        "Use this when starting a new phase of work, when context is getting "
        "large, or at natural task boundaries. The new session inherits a "
        "structured summary of goals, decisions, entities, and open TODOs."
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


def handle_fork_session(args: dict[str, Any]) -> str:
    title = args.get("title", "Continued session")
    summary_focus = args.get("summary_focus", "")
    history = CURRENT_HISTORY.get([])
    msgs = history

    goals = []
    decisions = []
    entities = []
    todos = []

    for m in msgs:
        role = getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else None)
        content = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else None)
        if not isinstance(content, str) or not content:
            continue
        if role == "user":
            if any(kw in content.lower() for kw in ("implement", "create", "build", "fix", "add", "refactor", "write")):
                goals.append(content[:200])
        elif role == "assistant":
            if any(kw in content.lower() for kw in ("decision:", "decided", "let's use", "we'll go with", "approach:")):
                decisions.append(content[:200])
            for word in content.split():
                if word.endswith((".py", ".ts", ".tsx", ".js", ".json", ".md", ".toml")):
                    if word not in entities:
                        entities.append(word)
            if any(kw in content.lower() for kw in ("todo:", "remaining:", "still need", "next step")):
                todos.append(content[:200])

    parts = ["## Session Memory (auto-generated summary)\n"]
    if goals:
        parts.append("- **Goals:**")
        for g in goals[-5:]:
            parts.append(f"  - {g}")
    if decisions:
        parts.append("- **Key Decisions:**")
        for d in decisions[-5:]:
            parts.append(f"  - {d}")
    if entities:
        parts.append("- **Entities (files):**")
        for e in entities[-15:]:
            parts.append(f"  - `{e}`")
    if todos:
        parts.append("- **Open TODOs:**")
        for t in todos[-5:]:
            parts.append(f"  - {t}")
    if summary_focus:
        parts.append(f"- **Focus area:** {summary_focus}")
    summary = "\n".join(parts)

    return json.dumps({
        "ok": True,
        "title": title,
        "summary": summary,
        "source_message_count": len(msgs),
        "instructions": (
            "A fork_session tool call was made. The backend should create a "
            "child session with this summary as context. The agent should "
            "inform the user that a new session was started for the next phase."
        ),
    }, ensure_ascii=False)
