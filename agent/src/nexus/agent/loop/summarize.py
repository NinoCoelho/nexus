from __future__ import annotations

import json
import logging

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

_KEEP_RECENT_N = 20


async def summarize_older_turns(
    messages: list[ChatMessage],
    provider: LLMProvider,
    model_id: str | None = None,
    *,
    keep_recent_n: int = _KEEP_RECENT_N,
) -> tuple[str, list[ChatMessage]]:
    if len(messages) <= keep_recent_n:
        return "", list(messages)

    old_messages = messages[:-keep_recent_n]
    recent_messages = messages[-keep_recent_n:]

    conversation_text = _format_for_summarization(old_messages)

    summary = await _call_summarizer(provider, conversation_text, model_id)

    if not summary:
        log.warning("summarization returned empty — keeping full history")
        return "", list(messages)

    log.info(
        "summarized %d old messages into %d chars (keeping %d recent)",
        len(old_messages), len(summary), len(recent_messages),
    )
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
) -> str:
    from ..llm import ChatMessage as Msg, Role as R

    messages = [
        Msg(role=R.SYSTEM, content=_SUMMARY_SYSTEM_PROMPT),
        Msg(role=R.USER, content=f"Summarize this conversation:\n\n{conversation}"),
    ]
    try:
        response = await provider.chat(
            messages,
            model=model_id,
            max_tokens=1024,
            tools=[],
        )
        return (response.content or "").strip()
    except Exception:
        log.warning("summarization LLM call failed", exc_info=True)
        return ""
