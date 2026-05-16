from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ..llm import ChatMessage, LLMProvider, Role

log = logging.getLogger(__name__)

_SUMMARY_SYSTEM_PROMPT = """\
You are a session memory compressor. Given a conversation history, produce a \
structured summary following this exact schema. Be concise — this summary \
replaces the full conversation in the model's context window.

## Session Memory

- **Goals:** [what the user asked for — current objectives]
- **Decisions:** [key choices made + brief rationale]
- **Entities:** [files, APIs, tools, URLs referenced — list with current state]
- **Open TODOs:** [pending items, unfinished work]
- **Last state:** [what was happening right before this summary — current task progress]

Rules:
- Preserve exact file paths, function names, and URLs.
- Preserve any error messages or stack traces mentioned.
- Note which tools were used and their outcomes.
- If the user expressed preferences or constraints, include them.
- Keep total output under 800 tokens.
"""

_UPDATE_SYSTEM_PROMPT = """\
You are a session memory compressor updating an existing summary with new \
conversation turns. Preserve all still-relevant information from the previous \
summary and merge in new facts. Remove completed TODOs and update the Last \
state. Follow the same schema:

## Session Memory

- **Goals:** [current objectives, updated if changed]
- **Decisions:** [all key choices, old and new]
- **Entities:** [all files/APIs/tools/URLs referenced so far]
- **Open TODOs:** [only pending items — drop completed ones]
- **Last state:** [what is happening now]

Rules:
- Preserve exact file paths, function names, and URLs.
- Preserve any error messages or stack traces mentioned.
- Note which tools were used and their outcomes.
- If the user expressed preferences or constraints, include them.
- Keep total output under 800 tokens.
"""

_KEEP_RECENT_N = 20

_SESSION_MEMORY_DIR = Path("~/.nexus/vault/.session-memory").expanduser()

_SUMMARY_PREFIX = "[Session Memory"


def _extract_existing_summary(messages: list[ChatMessage]) -> str | None:
    for msg in messages:
        if msg.role == Role.SYSTEM and msg.content and msg.content.startswith(_SUMMARY_PREFIX):
            return msg.content
    return None


async def summarize_older_turns(
    messages: list[ChatMessage],
    provider: LLMProvider,
    model_id: str | None = None,
    *,
    session_id: str | None = None,
    keep_recent_n: int = _KEEP_RECENT_N,
) -> tuple[str, list[ChatMessage]]:
    min_tail = 4
    effective_keep = min(keep_recent_n, max(min_tail, len(messages) // 2))
    if len(messages) <= effective_keep:
        return "", list(messages)

    old_messages = messages[:-effective_keep]
    recent_messages = messages[-effective_keep:]

    existing_summary = _extract_existing_summary(messages)
    conversation_text = _format_for_summarization(old_messages)

    summary = await _call_summarizer(
        provider, conversation_text, model_id,
        existing_summary=existing_summary,
    )

    if not summary:
        log.warning("summarization returned empty — keeping full history")
        return "", list(messages)

    log.info(
        "summarized %d old messages into %d chars (keeping %d recent)%s",
        len(old_messages), len(summary), len(recent_messages),
        " [iterative update]" if existing_summary else "",
    )

    if session_id:
        persist_session_summary(session_id, summary)

    return summary, recent_messages


def _format_for_summarization(messages: list[ChatMessage]) -> str:
    parts: list[str] = []
    for m in messages:
        role = m.role.value if hasattr(m.role, "value") else str(m.role)
        content = m.content or ""
        if not isinstance(content, str):
            try:
                content = json.dumps(content, ensure_ascii=False)
            except (TypeError, ValueError):
                content = str(content)

        if m.role == Role.TOOL:
            name = m.name or "tool"
            if len(content) > 500:
                content = content[:500] + f"... [{len(content)} chars total]"
            parts.append(f"[{role} ({name})]: {content}")
        elif m.tool_calls:
            tc_names = ", ".join(tc.name for tc in m.tool_calls if tc.name)
            parts.append(f"[{role} (called: {tc_names})]: {content[:500]}")
        else:
            if len(content) > 1000:
                content = content[:1000] + f"... [{len(content)} chars total]"
            parts.append(f"[{role}]: {content}")

    return "\n\n".join(parts)


async def _call_summarizer(
    provider: LLMProvider,
    conversation: str,
    model_id: str | None,
    *,
    existing_summary: str | None = None,
) -> str:
    from ..llm import ChatMessage as Msg, Role as R

    if existing_summary:
        system_prompt = _UPDATE_SYSTEM_PROMPT
        user_content = (
            f"Here is the previous session summary:\n\n{existing_summary}\n\n"
            f"---\n\nNow update it with these new conversation turns:\n\n{conversation}"
        )
    else:
        system_prompt = _SUMMARY_SYSTEM_PROMPT
        user_content = f"Summarize this conversation:\n\n{conversation}"

    messages = [
        Msg(role=R.SYSTEM, content=system_prompt),
        Msg(role=R.USER, content=user_content),
    ]
    try:
        response = await provider.chat(
            messages,
            model=model_id,
            max_tokens=1024,
            tools=[],
        )
        content = (response.content or "").strip()
        if not content:
            log.warning("summarization returned empty content for model=%s", model_id)
        return content
    except Exception as exc:
        from ...error_classifier import is_budget_exceeded
        if is_budget_exceeded(exc):
            raise
        log.warning("summarization LLM call failed for model=%s", model_id, exc_info=True)
        return ""


def persist_session_summary(session_id: str, summary: str, *, model_id: str | None = None) -> None:
    try:
        _SESSION_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        path = _SESSION_MEMORY_DIR / f"{session_id}.md"
        now = datetime.now(timezone.utc).isoformat()
        frontmatter = (
            "---\n"
            f"session_id: {session_id}\n"
            f"updated_at: {now}\n"
        )
        if model_id:
            frontmatter += f"model: {model_id}\n"
        frontmatter += "---\n\n"
        path.write_text(frontmatter + summary, encoding="utf-8")
        log.debug("persisted session summary to %s", path)
    except Exception:
        log.debug("failed to persist session summary", exc_info=True)
