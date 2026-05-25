"""Skill refinement phase — identifies repeated workflows and drafts skills."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path  # noqa: F401 — kept for test monkeypatching
from typing import Any

from ..home import skills_dir as _home_skills_dir, sessions_db, dream_suggestions_dir

log = logging.getLogger(__name__)

_SKILL_REFINE_SYSTEM_PROMPT = """\
You are a skill discovery engine for a personal AI agent called Nexus.

You receive:
1. A list of the agent's existing skills (name + description).
2. Summaries of recent chat sessions showing tool calls and outcomes.

Your job is to identify **repeated multi-step workflows** that are NOT already
covered by an existing skill and would benefit from being captured as a reusable
skill.

A valid candidate must:
- Appear in at least 3 distinct sessions.
- Span at least 2 different days.
- Involve a coherent multi-step sequence (not a single tool call).

Output ONLY valid JSON:
{{
  "suggestions": [
    {{
      "name": "skill-name",
      "description": "one-line description",
      "reason": "why this should be a skill",
      "evidence_sessions": ["session-ids..."],
      "draft_content": "full SKILL.md content with frontmatter"
    }}
  ]
}}

Rules:
- Max 3 suggestions per run.
- The draft_content must include valid YAML frontmatter with name and description.
- If nothing qualifies, return {{"suggestions": []}}.
- Today's date: {today}
"""

_MIN_SESSIONS = 3
_MIN_DAYS = 2


@dataclass
class SkillSuggestion:
    name: str
    description: str
    reason: str
    evidence_sessions: list[str] = field(default_factory=list)
    draft_content: str = ""
    created: bool = False
    error: str | None = None


@dataclass
class SkillRefineResult:
    suggestions: list[SkillSuggestion] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0


async def run_skill_refinement(
    *,
    provider: Any,
    state_store: Any,
    model_id: str | None = None,
    max_tokens: int = 4000,
    context_budget: int = 6000,
) -> SkillRefineResult:
    existing_skills = await asyncio.to_thread(_load_existing_skills)
    session_summaries = await asyncio.to_thread(_load_session_summaries, limit=30)

    if len(session_summaries) < _MIN_SESSIONS:
        log.info("dream/skill_refine: fewer than %d sessions, skipping", _MIN_SESSIONS)
        return SkillRefineResult()

    context = _build_context(existing_skills, session_summaries)
    if len(context) > context_budget:
        context = context[:context_budget]

    from ..agent.llm import ChatMessage as LLMChatMessage, Role

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    system = _SKILL_REFINE_SYSTEM_PROMPT.format(today=today)

    messages = [
        LLMChatMessage(role=Role.SYSTEM, content=system),
        LLMChatMessage(role=Role.USER, content=context),
    ]

    try:
        response = await provider.chat(
            messages, model=model_id, max_tokens=max_tokens,
        )
    except Exception:
        log.exception("dream/skill_refine: LLM call failed")
        return SkillRefineResult(errors=["LLM call failed"])

    raw = response.content.strip()
    tokens_in = response.usage.input_tokens
    tokens_out = response.usage.output_tokens
    parsed = _extract_json(raw)
    if parsed is None:
        log.warning("dream/skill_refine: failed to parse LLM output")
        return SkillRefineResult(errors=["Failed to parse response"], tokens_in=tokens_in, tokens_out=tokens_out)

    suggestions_raw = parsed.get("suggestions", [])
    if not isinstance(suggestions_raw, list):
        return SkillRefineResult(errors=["'suggestions' is not a list"], tokens_in=tokens_in, tokens_out=tokens_out)

    result = SkillRefineResult(tokens_in=tokens_in, tokens_out=tokens_out)
    for item in suggestions_raw[:3]:
        name = item.get("name", "")
        description = item.get("description", "")
        reason = item.get("reason", "")
        draft = item.get("draft_content", "")
        evidence = item.get("evidence_sessions", [])

        if not name or not draft:
            continue

        suggestion = SkillSuggestion(
            name=name,
            description=description,
            reason=reason,
            evidence_sessions=evidence,
            draft_content=draft,
        )

        content_hash = _hash_suggestion(name, description)
        if await asyncio.to_thread(state_store.has_explored, content_hash):
            log.debug("dream/skill_refine: skipping duplicate '%s'", name)
            continue

        if await asyncio.to_thread(_skill_exists, name):
            log.debug("dream/skill_refine: skill '%s' already exists", name)
            continue

        await asyncio.to_thread(_write_skill_suggestion, suggestion)
        await asyncio.to_thread(state_store.mark_explored, content_hash)
        result.suggestions.append(suggestion)

    return result


def _load_existing_skills() -> list[dict[str, str]]:
    skills_dir = _home_skills_dir()
    if not skills_dir.exists():
        return []
    skills = []
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            import frontmatter
            post = frontmatter.load(str(skill_md))
            name = post.get("name", skill_dir.name)
            description = post.get("description", "")
            skills.append({"name": name, "description": description})
        except Exception:
            skills.append({"name": skill_dir.name, "description": ""})
    return skills


def _load_session_summaries(*, limit: int = 30) -> list[dict[str, str]]:
    from ._shared import load_session_summaries
    return load_session_summaries(
        db_path=sessions_db(),
        limit=limit,
        roles=("user", "assistant"),
        max_content_length=2000,
        preview_len=400,
        include_date=True,
    )


def _build_context(
    skills: list[dict[str, str]],
    sessions: list[dict[str, str]],
) -> str:
    parts = []
    if skills:
        parts.append("## Existing Skills")
        for s in skills:
            parts.append(f"- **{s['name']}**: {s['description'][:80]}")
        parts.append("")
    parts.append("## Recent Sessions")
    for s in sessions:
        parts.append(f"- **{s['title']}** (id: {s['session_id']}, date: {s['date']})")
        if s["preview"]:
            parts.append(f"  {s['preview'][:200]}")
    return "\n".join(parts)


def _skill_exists(name: str) -> bool:
    return (_home_skills_dir() / name / "SKILL.md").exists()


def _write_skill_suggestion(suggestion: SkillSuggestion) -> None:
    suggestions_dir = dream_suggestions_dir()
    suggestions_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    filename = f"{suggestion.name}-{ts}.md"
    path = suggestions_dir / filename

    content = (
        f"---\ntags: [skill-suggestion, dream-generated]\n"
        f"skill_name: {suggestion.name}\n---\n\n"
        f"# Skill Suggestion: {suggestion.name}\n\n"
        f"**Description:** {suggestion.description}\n\n"
        f"**Reason:** {suggestion.reason}\n\n"
        f"**Evidence sessions:** {', '.join(suggestion.evidence_sessions)}\n\n"
        f"## Draft SKILL.md\n\n{ suggestion.draft_content}\n"
    )
    path.write_text(content, encoding="utf-8")
    log.info("dream/skill_refine: wrote suggestion '%s'", suggestion.name)


def _hash_suggestion(name: str, description: str) -> str:
    import hashlib
    return hashlib.sha256(f"skill:{name}|{description}".encode()).hexdigest()[:32]


def _extract_json(text: str) -> dict[str, Any] | None:
    from ._shared import extract_json
    return extract_json(text)
