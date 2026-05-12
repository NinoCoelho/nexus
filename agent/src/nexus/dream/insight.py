"""Insight extraction phase — cross-session pattern analysis."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_INSIGHT_SYSTEM_PROMPT = """\
You are an insight extraction engine for a personal AI agent. You receive
summaries of recent chat sessions and the agent's current memory notes.

Your job is to identify **cross-session patterns** that no single session
could see. Look for:

1. **Recurring workflows** — tasks the user does repeatedly across sessions
2. **Recurring errors** — things that go wrong more than once
3. **User preferences** — patterns in how the user likes things done
4. **Project themes** — what the user is working on across sessions
5. **Tool patterns** — tools used frequently together

Output ONLY valid JSON:
{{
  "insights": [
    {{
      "title": "short descriptive title",
      "body": "1-3 sentence insight with specific evidence",
      "confidence": "high" | "medium" | "low",
      "tags": ["tag1", "tag2"]
    }}
  ]
}}

Rules:
- Max 5 insights per extraction.
- Each insight must reference evidence from at least 2 sessions.
- Use "high" confidence only for patterns seen 3+ times.
- Use "low" confidence for interesting but uncertain patterns.
- If nothing meaningful emerges, return {{"insights": []}}.
- Today's date: {today}
"""

_DREAM_INSIGHTS_DIR = Path.home() / ".nexus" / "vault" / "memory" / "dream-insights"
_INSIGHT_EXPIRY_DAYS = 30


@dataclass
class Insight:
    title: str
    body: str
    confidence: str
    tags: list[str] = field(default_factory=list)


@dataclass
class InsightResult:
    insights: list[Insight] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0


async def run_insight_extraction(
    *,
    provider: Any,
    state_store: Any,
    model_id: str | None = None,
    max_tokens: int = 4000,
    context_budget: int = 6000,
    since: datetime | None = None,
) -> InsightResult:
    session_summaries = _load_recent_sessions(since=since, limit=20)
    if len(session_summaries) < 2:
        log.info("dream/insight: fewer than 2 sessions, skipping")
        return InsightResult()

    memory_notes = _load_memory_summaries(limit=10)

    combined_context = _build_context(session_summaries, memory_notes)
    if len(combined_context) > context_budget:
        combined_context = combined_context[:context_budget]

    from ..agent.llm import ChatMessage as LLMChatMessage, Role

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    system = _INSIGHT_SYSTEM_PROMPT.format(today=today)

    messages = [
        LLMChatMessage(role=Role.SYSTEM, content=system),
        LLMChatMessage(role=Role.USER, content=combined_context),
    ]

    try:
        response = await provider.chat(
            messages, model=model_id, max_tokens=max_tokens,
        )
    except Exception:
        log.exception("dream/insight: LLM call failed")
        return InsightResult(errors=["LLM call failed"])

    raw = response.content.strip()
    tokens_in = response.usage.input_tokens
    tokens_out = response.usage.output_tokens
    parsed = _extract_json(raw)
    if parsed is None:
        log.warning("dream/insight: failed to parse LLM output as JSON")
        return InsightResult(errors=["Failed to parse insight response"], tokens_in=tokens_in, tokens_out=tokens_out)

    insights_raw = parsed.get("insights", [])
    if not isinstance(insights_raw, list):
        return InsightResult(errors=["'insights' is not a list"], tokens_in=tokens_in, tokens_out=tokens_out)

    result = InsightResult(tokens_in=tokens_in, tokens_out=tokens_out)
    for item in insights_raw[:5]:
        title = item.get("title", "")
        body = item.get("body", "")
        confidence = item.get("confidence", "low")
        tags = item.get("tags", [])

        if not title or not body:
            continue

        content_hash = _hash_insight(title, body)
        if _insight_exists(title):
            log.debug("dream/insight: skipping duplicate insight '%s'", title)
            continue

        insight = Insight(
            title=title,
            body=body,
            confidence=confidence,
            tags=tags + ["dream-insight", "auto-generated"],
        )

        _persist_insight(insight)
        state_store.mark_explored(content_hash)
        result.insights.append(insight)

    _expire_old_insights()

    return result


def _load_recent_sessions(
    *, since: datetime | None = None, limit: int = 20,
) -> list[dict[str, str]]:
    try:
        import sqlite3
        db_path = Path.home() / ".nexus" / "sessions.sqlite"
        if not db_path.exists():
            return []
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            query = """
                SELECT s.id, s.title, s.updated_at,
                       GROUP_CONCAT(m.content, ' ') AS messages
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id
                    AND m.role IN ('user', 'assistant')
                    AND LENGTH(m.content) < 2000
                WHERE COALESCE(s.hidden, 0) = 0
            """
            params: list[Any] = []
            if since:
                query += " AND s.updated_at > ?"
                params.append(since.strftime("%Y-%m-%dT%H:%M:%S"))
            query += """
                GROUP BY s.id
                ORDER BY s.updated_at DESC
                LIMIT ?
            """
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            summaries = []
            for r in rows:
                title = r["title"] or "Untitled"
                msgs = r["messages"] or ""
                text = msgs[:500]
                summaries.append({
                    "session_id": r["id"][:8],
                    "title": title,
                    "preview": text,
                })
            return summaries
        finally:
            conn.close()
    except Exception:
        log.exception("dream/insight: session load failed")
        return []


def _load_memory_summaries(*, limit: int = 10) -> list[dict[str, str]]:
    memory_dir = Path.home() / ".nexus" / "vault" / "memory"
    if not memory_dir.exists():
        return []
    notes = []
    for md_file in sorted(memory_dir.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        if "dream-insights" in str(md_file):
            continue
        try:
            content = md_file.read_text(encoding="utf-8")[:300]
            rel = str(md_file.relative_to(memory_dir).with_suffix(""))
            notes.append({"key": rel, "preview": content})
        except Exception:
            continue
        if len(notes) >= limit:
            break
    return notes


def _build_context(
    sessions: list[dict[str, str]],
    memories: list[dict[str, str]],
) -> str:
    parts = []
    if memories:
        parts.append("## Current Memory Notes")
        for m in memories:
            parts.append(f"- **{m['key']}**: {m['preview'][:100]}")
        parts.append("")
    parts.append("## Recent Sessions")
    for s in sessions:
        parts.append(f"- **{s['title']}** (id: {s['session_id']})")
        if s["preview"]:
            parts.append(f"  {s['preview'][:200]}")
    return "\n".join(parts)


def _persist_insight(insight: Insight) -> None:
    _DREAM_INSIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", insight.title.lower())[:40]
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    filename = f"{slug}-{ts}.md"
    path = _DREAM_INSIGHTS_DIR / filename

    tags_str = ", ".join(insight.tags)
    content = (
        f"---\ntags: [{tags_str}]\nconfidence: {insight.confidence}\n"
        f"dream-generated: true\n---\n\n"
        f"# {insight.title}\n\n{insight.body}\n"
    )
    path.write_text(content, encoding="utf-8")
    log.info("dream/insight: wrote %s", filename)


def _expire_old_insights() -> int:
    if not _DREAM_INSIGHTS_DIR.exists():
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=_INSIGHT_EXPIRY_DAYS)
    expired = 0
    for f in _DREAM_INSIGHTS_DIR.glob("*.md"):
        try:
            stat = f.stat()
            from datetime import timezone
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                content = f.read_text(encoding="utf-8")
                if "dream-generated: true" in content:
                    f.unlink()
                    expired += 1
        except Exception:
            continue
    return expired


def _hash_insight(title: str, body: str) -> str:
    return hashlib.sha256(f"{title}|{body}".encode()).hexdigest()[:32]


def _extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    candidate = text.strip()
    if candidate.startswith("```"):
        first_nl = candidate.find("\n")
        if first_nl >= 0:
            candidate = candidate[first_nl + 1:]
        last_fence = candidate.rfind("```")
        if last_fence > 0:
            candidate = candidate[:last_fence]
    candidate = candidate.strip()
    if not candidate:
        return None
    brace = candidate.find("{")
    bracket = candidate.find("[")
    if brace >= 0 and (bracket < 0 or brace <= bracket):
        candidate = candidate[brace:]
    elif bracket >= 0:
        candidate = candidate[bracket:]
    try:
        result = json.loads(candidate)
        if isinstance(result, dict):
            return result
        if isinstance(result, list) and len(result) > 0 and isinstance(result[0], dict):
            return result[0]
    except json.JSONDecodeError:
        pass
    try:
        brace = candidate.rfind("}")
        if brace >= 0:
            result = json.loads(candidate[: brace + 1])
            if isinstance(result, dict):
                return result
    except json.JSONDecodeError:
        pass
    return None
