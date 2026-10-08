from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import datetime, timezone

from ..llm import ChatMessage, LLMProvider, Role
from nexus.home import vault_session_memory as _session_memory_fn
from nexus.agent.loop.relevance import _content_text, score_messages
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


# Max chars of a message fed to the embedder. fastembed truncates at 512
# tokens regardless, but capping here bounds the per-message cost and keeps
# the embedding semantically meaningful (not a clipped tail).
_EMBED_TEXT_CAP = 1500


# Message embeddings are cached by content hash. Message bodies are immutable
# (ChatMessage is frozen) and compaction re-scores the *same* history on every
# pass, so without this the whole transcript is re-embedded each time — the
# dominant cost of relevance scoring on a long session. Bounded FIFO; vectors
# are a few hundred floats each.
_EMBED_CACHE: dict[str, list[float]] = {}
_EMBED_CACHE_MAX = 2_048


def _embed_cache_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _embed_cache_put(key: str, vec: list[float]) -> None:
    if key in _EMBED_CACHE:
        return
    if len(_EMBED_CACHE) >= _EMBED_CACHE_MAX:
        # Drop the oldest ~10% in insertion order so this isn't O(n) per put.
        for stale in list(_EMBED_CACHE)[: max(1, _EMBED_CACHE_MAX // 10)]:
            _EMBED_CACHE.pop(stale, None)
    _EMBED_CACHE[key] = vec


def _cosine(a: list[float], b: list[float]) -> float:
    anorm = math.sqrt(sum(x * x for x in a)) or 1.0
    bnorm = math.sqrt(sum(x * x for x in b)) or 1.0
    dot = sum(x * y for x, y in zip(a, b))
    # Negative cosine (anti-correlated) is "not relevant" → 0.
    return max(0.0, min(1.0, dot / (anorm * bnorm)))


async def _compute_semantic_sim(
    messages: list[ChatMessage],
    query: str,
    embedder: object,
) -> dict[int, float] | None:
    """Embedding cosine similarity of each message to the query.

    Returns ``{index: similarity_0_to_1}`` (only for messages with non-empty
    text), or ``None`` on any failure — callers fall back to regex-only
    scoring. Pure math given an embedder; the ``knowledge``-feature gating and
    embedder acquisition live in :func:`summarize_older_turns` so this stays
    unit-testable with a fake embedder.

    Per-message vectors are memoized by content hash, so a repeat compaction
    on the same session only embeds what is new.
    """
    texts: list[str] = []
    idx_map: list[int] = []
    for i, m in enumerate(messages):
        t = _content_text(m)
        if t and t.strip():
            texts.append(t[:_EMBED_TEXT_CAP])
            idx_map.append(i)
    if not texts:
        return None

    query_text = query[:_EMBED_TEXT_CAP]
    keys = [_embed_cache_key(t) for t in texts]
    # The query embedding is request-specific; never served from cache.
    missing_positions = [pos for pos, key in enumerate(keys) if key not in _EMBED_CACHE]

    async def _embed(batch: list[str]) -> list[list[float]] | None:
        try:
            return await embedder.embed(batch)  # type: ignore[attr-defined]
        except Exception:
            log.debug("semantic similarity embedding failed", exc_info=True)
            return None

    to_embed = [query_text, *(texts[p] for p in missing_positions)]
    vecs = await _embed(to_embed)
    if not vecs:
        return None
    if len(vecs) != len(to_embed):
        # The embedder didn't return one vector per input. If we had asked for
        # a partial batch, the cache is the only thing that could have made it
        # partial, so retry once asking for everything before giving up.
        if len(missing_positions) == len(texts):
            return None
        missing_positions = list(range(len(texts)))
        to_embed = [query_text, *texts]
        vecs = await _embed(to_embed)
        if not vecs or len(vecs) != len(to_embed):
            return None

    qv = vecs[0]
    for offset, pos in enumerate(missing_positions):
        _embed_cache_put(keys[pos], vecs[offset + 1])

    out: dict[int, float] = {}
    for pos, idx in enumerate(idx_map):
        mv = _EMBED_CACHE.get(keys[pos])
        if mv is None:
            continue
        out[idx] = _cosine(qv, mv)
    return out


def reset_embedding_cache() -> None:
    """Clear the memoized message embeddings (tests / embedder model change)."""
    _EMBED_CACHE.clear()


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

    # Phase 4: when the builtin embedder is available, score meaning-level
    # (semantic) relevance in addition to regex entity overlap. Catches an
    # old tool result that shares intent with the current query but no
    # surface tokens. Best-effort — any failure degrades silently to
    # regex-only scoring.
    semantic_sim: dict[int, float] | None = None
    if query:
        try:
            from ..builtin_embedder import get_builtin_embedder

            semantic_sim = await _compute_semantic_sim(
                messages, query, get_builtin_embedder()
            )
        except Exception:
            log.debug("semantic relevance unavailable; using regex-only", exc_info=True)
            semantic_sim = None

    scores = score_messages(messages, query=query, semantic_sim=semantic_sim)
    plan = partition(messages, scores, recent_k=keep_recent_n)

    if plan.is_noop():
        return "", list(messages)

    summarize_msgs = [messages[i] for i in plan.summarize]
    kept_idx = plan.kept_indices()

    # The drop bucket (scrape garbage) leaves the window without passing
    # through the summary, so it needs its own journal entry — otherwise it is
    # the one lossy path with no recovery record, and it takes the whole
    # compaction unit with it, including the assistant's reasoning.
    if session_id and plan.drop:
        persist_summary_part(
            session_id, [messages[i] for i in plan.drop], reason="dropped"
        )

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
        archive = persist_summary_part(session_id, summarize_msgs, reason="summarized")
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


def _compact_model() -> str:
    """Optional ``[agent].compact_model`` — a cheap/fast model for summaries.

    Summarization sits on the critical path of a user turn, so running it on
    the main (often large, reasoning-capable) model costs seconds for a
    1024-token structured note. Empty string means "use the turn's model".
    """
    try:
        from ...config_file import load_cached

        cfg = load_cached()
        return str(getattr(getattr(cfg, "agent", None), "compact_model", "") or "")
    except Exception:  # noqa: BLE001 — config problems must not block compaction
        return ""


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

    # Try the dedicated compaction model first, then the turn's own model. The
    # configured fast model may belong to a provider this one can't reach, so
    # a failure there must fall through rather than lose the summary.
    candidates: list[str | None] = []
    fast = _compact_model()
    if fast and fast != model_id:
        candidates.append(fast)
    candidates.append(model_id)

    for attempt, candidate in enumerate(candidates):
        try:
            response = await provider.chat(
                messages,
                model=candidate,
                max_tokens=1024,
                tools=[],
            )
        except Exception as exc:
            from ...error_classifier import is_budget_exceeded
            if is_budget_exceeded(exc):
                raise
            log.warning(
                "summarization LLM call failed for model=%s%s",
                candidate,
                " (falling back)" if attempt + 1 < len(candidates) else "",
                exc_info=True,
            )
            continue
        content = (response.content or "").strip()
        if content:
            return content
        log.warning("summarization returned empty content for model=%s", candidate)
    return ""


def persist_session_summary(session_id: str, summary: str, *, model_id: str | None = None) -> str | None:
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
        return str(path)
    except Exception:
        log.debug("failed to persist session summary", exc_info=True)
        return None


def load_session_summary(session_id: str) -> str | None:
    """Read the persisted session-memory summary body, or None.

    The frontmatter (session_id/updated_at/model) is stripped — callers
    inject the body as the ``[Session Memory`` system message. A missing or
    empty file is not an error.
    """
    try:
        path = _session_memory_fn() / f"{session_id}.md"
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return None
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                text = text[end + 4:].strip()
        return text or None
    except Exception:
        log.debug("failed to load session summary", exc_info=True)
        return None


def persist_summary_part(
    session_id: str, summarized: list[ChatMessage], *, reason: str = "summarized"
) -> str | None:
    """Append the verbatim messages being removed to a recovery archive.

    Summarization is lossy by design, but it shouldn't be a black hole: every
    collapse is journaled as one JSONL record under
    ``~/.nexus/vault/.session-memory/.parts/{session_id}.jsonl`` so a dropped
    detail can always be recovered. Mirrors the ``.tool-cache`` reversibility
    the tool-shrink path already provides.

    ``reason`` records *why* the messages left the window — ``summarized``
    (folded into the session note), ``dropped`` (scored as scrape garbage) or
    ``hard_trim`` (elided to force a fit) — so a recovery read can tell an
    intentional compression from a capacity eviction.

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
            "reason": reason,
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
