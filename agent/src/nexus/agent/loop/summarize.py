from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..llm import ChatMessage, LLMProvider, Role
from nexus.home import vault_session_memory as _session_memory_fn
from nexus.agent.loop.relevance import score_messages
from nexus.agent.loop.retention import partition

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

_SUMMARY_PREFIX = "[Session Memory"


def _extract_existing_summary(messages: list[ChatMessage]) -> str | None:
    for msg in messages:
        if msg.role == Role.SYSTEM and msg.content and msg.content.startswith(_SUMMARY_PREFIX):
            return msg.content
    return None


def _adjust_split_for_tool_pairs(messages: list[ChatMessage], split_idx: int) -> int:
    """Deprecated: kept for any out-of-tree callers. Retention planning
    (``loop/retention.py``) now guarantees tool-pair integrity via atomic
    compaction units, so the positional split this served is gone."""
    n = len(messages)
    if split_idx <= 0 or split_idx >= n:
        return split_idx
    changed = True
    while changed:
        changed = False
        if messages[split_idx].role == Role.TOOL and split_idx > 0:
            split_idx -= 1
            changed = True
            continue
        if (split_idx > 0
                and messages[split_idx - 1].role == Role.ASSISTANT
                and messages[split_idx - 1].tool_calls):
            split_idx -= 1
            changed = True
    return split_idx


def _last_user_text(messages: list[ChatMessage]) -> str | None:
    """The most recent USER message's text — the current turn's focus.

    Drives entity-overlap relevance scoring when the caller doesn't pass an
    explicit query.
    """
    for m in reversed(messages):
        if m.role == Role.USER:
            content = m.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(p.text for p in content if getattr(p, "text", None))
            return None
    return None


def _find_summary_index(messages: list[ChatMessage]) -> int | None:
    """Index of the existing session-memory SYSTEM message, if any."""
    for i, m in enumerate(messages):
        if m.role == Role.SYSTEM and m.content and m.content.startswith(_SUMMARY_PREFIX):
            return i
    return None


async def summarize_older_turns(
    messages: list[ChatMessage],
    provider: LLMProvider,
    model_id: str | None = None,
    *,
    session_id: str | None = None,
    keep_recent_n: int = _KEEP_RECENT_N,
    query: str | None = None,
) -> tuple[str, list[ChatMessage]]:
    """Summarize low-relevance older messages into a session-memory note.

    Uses relevance-ranked retention (``loop/relevance.py`` +
    ``loop/retention.py``) instead of a fixed positional split: messages are
    bucketed into protected / recent / relevant / summarize / drop, so a
    high-signal old message survives verbatim while only the genuinely
    low-relevance tail is collapsed into the summary.

    Returns ``(summary_text, kept_messages)``. ``kept_messages`` are the
    verbatim survivors (no summary message — the caller prepends that). On any
    failure the full input is returned unchanged so the turn degrades to
    "no summarization" rather than dropping context.
    """
    if len(messages) <= keep_recent_n:
        return "", list(messages)

    if query is None:
        query = _last_user_text(messages)

    scores = score_messages(messages, query=query)
    plan = partition(messages, scores, recent_k=keep_recent_n)

    if plan.is_noop():
        return "", list(messages)

    summarize_msgs = [messages[i] for i in plan.summarize]
    kept_idx = plan.kept_indices()

    # Nothing to summarize — but there may still be garbage drops to apply.
    if not summarize_msgs:
        return "", [messages[i] for i in kept_idx]

    existing_summary = _extract_existing_summary(messages)
    conversation_text = _format_for_summarization(summarize_msgs)

    summary = await _call_summarizer(
        provider, conversation_text, model_id,
        existing_summary=existing_summary,
    )

    if not summary:
        log.warning("summarization returned empty — keeping full history")
        return "", list(messages)

    log.info(
        "summarized %d low-relevance messages into %d chars (keeping %d verbatim)%s",
        len(summarize_msgs), len(summary), len(kept_idx),
        " [iterative update]" if existing_summary else "",
    )

    if session_id:
        persist_session_summary(session_id, summary)
        archive = persist_summary_part(session_id, summarize_msgs)
        if archive:
            log.debug("archived %d summarized messages to %s", len(summarize_msgs), archive)

    # The pre-existing summary SYSTEM message is protected by default; since
    # we just generated its successor, drop the stale one from the survivors
    # so it isn't carried alongside the fresh summary the caller prepends.
    existing_summary_idx = _find_summary_index(messages)
    if existing_summary_idx is not None:
        kept_idx = [i for i in kept_idx if i != existing_summary_idx]

    return summary, [messages[i] for i in kept_idx]


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
        sm_dir = _session_memory_fn()
        sm_dir.mkdir(parents=True, exist_ok=True)
        path = sm_dir / f"{session_id}.md"
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


def persist_summary_part(
    session_id: str, summarized: list[ChatMessage]
) -> str | None:
    """Append the verbatim messages being summarized to a recovery archive.

    Summarization is lossy by design, but it shouldn't be a black hole: every
    collapse is journaled as one JSONL record under
    ``~/.nexus/session-memory/.parts/{session_id}.jsonl`` so a dropped detail
    can always be recovered. Mirrors the ``.tool-cache`` reversibility the
    tool-shrink path already provides.

    Returns the archive path (for logging) or ``None`` on failure — never
    raises, since a persistence hiccup must not abort summarization.
    """
    if not summarized:
        return None
    try:
        parts_dir = _session_memory_fn() / ".parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        path = parts_dir / f"{session_id}.jsonl"
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "count": len(summarized),
            "messages": [
                m.model_dump(mode="json") if hasattr(m, "model_dump") else str(m)
                for m in summarized
            ],
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return str(path)
    except Exception:
        log.debug("failed to persist summary part", exc_info=True)
        return None
