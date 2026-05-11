"""Scenario rehearsal phase — pre-computes context for likely future tasks."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_REHEARSAL_SYSTEM_PROMPT = """\
You are a scenario rehearsal engine for a personal AI agent called Nexus.

You receive a list of recent sessions and extracted insights. Predict up to 3
likely tasks or queries the user might do next. For each, produce a short
pre-computed context note that would be useful if the user starts that task.

Output ONLY valid JSON:
{{
  "scenarios": [
    {{
      "title": "short title",
      "likelihood": "high" | "medium" | "low",
      "predicted_task": "what the user will likely do",
      "precomputed_note": "useful context, commands, or findings",
      "tags": ["tag1"]
    }}
  ]
}}

Rules:
- Max 3 scenarios.
- precomputed_note should be concise (2-4 sentences) and immediately actionable.
- Only "high" likelihood if the pattern appeared in the last 2 sessions.
- If uncertain, return {{"scenarios": []}}.
- Today's date: {today}
"""

_PRECOMPUTED_DIR = Path.home() / ".nexus" / "vault" / "memory" / "precomputed"
_PRECOMPUTED_EXPIRY_HOURS = 24


@dataclass
class Scenario:
    title: str
    likelihood: str
    predicted_task: str
    precomputed_note: str
    tags: list[str] = field(default_factory=list)


@dataclass
class RehearsalResult:
    scenarios: list[Scenario] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0


async def run_scenario_rehearsal(
    *,
    provider: Any,
    state_store: Any,
    model_id: str | None = None,
    max_tokens: int = 3000,
    context_budget: int = 4000,
) -> RehearsalResult:
    session_summaries = _load_recent_sessions(limit=10)
    if not session_summaries:
        log.info("dream/rehearse: no sessions, skipping")
        return RehearsalResult()

    insight_notes = _load_recent_insights(limit=3)
    context = _build_context(session_summaries, insight_notes)
    if len(context) > context_budget:
        context = context[:context_budget]

    from ..agent.llm import ChatMessage as LLMChatMessage, Role

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    system = _REHEARSAL_SYSTEM_PROMPT.format(today=today)

    messages = [
        LLMChatMessage(role=Role.SYSTEM, content=system),
        LLMChatMessage(role=Role.USER, content=context),
    ]

    try:
        response = await provider.chat(
            messages, model=model_id, max_tokens=max_tokens,
        )
    except Exception:
        log.exception("dream/rehearse: LLM call failed")
        return RehearsalResult(errors=["LLM call failed"])

    raw = response.content.strip()
    tokens_in = getattr(response, "input_tokens", 0) or 0
    tokens_out = getattr(response, "output_tokens", 0) or 0
    parsed = _extract_json(raw)
    if parsed is None:
        log.warning("dream/rehearse: failed to parse LLM output")
        return RehearsalResult(errors=["Failed to parse response"], tokens_in=tokens_in, tokens_out=tokens_out)

    scenarios_raw = parsed.get("scenarios", [])
    if not isinstance(scenarios_raw, list):
        return RehearsalResult(errors=["'scenarios' is not a list"], tokens_in=tokens_in, tokens_out=tokens_out)

    result = RehearsalResult(tokens_in=tokens_in, tokens_out=tokens_out)
    for item in scenarios_raw[:3]:
        title = item.get("title", "")
        likelihood = item.get("likelihood", "low")
        task = item.get("predicted_task", "")
        note = item.get("precomputed_note", "")
        tags = item.get("tags", [])

        if not title or not note:
            continue

        scenario = Scenario(
            title=title,
            likelihood=likelihood,
            predicted_task=task,
            precomputed_note=note,
            tags=tags + ["precomputed", "speculative"],
        )

        _persist_precomputed(scenario)
        result.scenarios.append(scenario)

    _expire_old_precomputed()

    return result


def _load_recent_sessions(*, limit: int = 10) -> list[dict[str, str]]:
    try:
        import sqlite3
        db_path = Path.home() / ".nexus" / "sessions.sqlite"
        if not db_path.exists():
            return []
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT s.id, s.title, s.updated_at, "
                "GROUP_CONCAT(m.content, ' ') AS messages "
                "FROM sessions s "
                "LEFT JOIN messages m ON m.session_id = s.id "
                "AND m.role = 'user' AND LENGTH(m.content) < 1000 "
                "WHERE COALESCE(s.hidden, 0) = 0 "
                "GROUP BY s.id ORDER BY s.updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            summaries = []
            for r in rows:
                msgs = r["messages"] or ""
                summaries.append({
                    "session_id": r["id"][:8],
                    "title": r["title"] or "Untitled",
                    "preview": msgs[:300],
                })
            return summaries
        finally:
            conn.close()
    except Exception:
        log.exception("dream/rehearse: session load failed")
        return []


def _load_recent_insights(*, limit: int = 3, insights_dir: Path | None = None) -> list[str]:
    if insights_dir is None:
        insights_dir = Path.home() / ".nexus" / "vault" / "memory" / "dream-insights"
    if not insights_dir.exists():
        return []
    notes = []
    for f in sorted(insights_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            content = f.read_text(encoding="utf-8")
            body = content
            if "---" in content:
                parts = content.split("---", 2)
                body = parts[2].strip() if len(parts) >= 3 else content
            notes.append(body[:200])
        except Exception:
            continue
        if len(notes) >= limit:
            break
    return notes


def _build_context(
    sessions: list[dict[str, str]],
    insights: list[str],
) -> str:
    parts = ["## Recent Sessions"]
    for s in sessions:
        parts.append(f"- **{s['title']}** (id: {s['session_id']})")
        if s["preview"]:
            parts.append(f"  {s['preview'][:150]}")
    if insights:
        parts.append("")
        parts.append("## Recent Dream Insights")
        for note in insights:
            parts.append(f"- {note[:150]}")
    return "\n".join(parts)


def _persist_precomputed(scenario: Scenario) -> None:
    _PRECOMPUTED_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", scenario.title.lower())[:30]
    filename = f"{slug}-{ts}.md"
    path = _PRECOMPUTED_DIR / filename

    tags_str = ", ".join(scenario.tags)
    content = (
        f"---\ntags: [{tags_str}]\nconfidence: speculative\n"
        f"expires_hours: 24\ndream-generated: true\n---\n\n"
        f"# {scenario.title}\n\n"
        f"**Predicted task:** {scenario.predicted_task}\n\n"
        f"{scenario.precomputed_note}\n"
    )
    path.write_text(content, encoding="utf-8")
    log.info("dream/rehearse: wrote precomputed '%s'", filename)


def _expire_old_precomputed() -> int:
    if not _PRECOMPUTED_DIR.exists():
        return 0
    cutoff = datetime.now(UTC) - __import__("datetime").timedelta(hours=_PRECOMPUTED_EXPIRY_HOURS)
    expired = 0
    for f in _PRECOMPUTED_DIR.glob("*.md"):
        try:
            content = f.read_text(encoding="utf-8")
            if "dream-generated: true" not in content:
                continue
            from datetime import timezone
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                f.unlink()
                expired += 1
        except Exception:
            continue
    return expired


def _extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    candidate = fence.group(1) if fence else text
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
