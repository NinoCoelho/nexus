"""Dream journal — writes structured markdown entries for each dream run."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from .consolidate import ConsolidationResult
from .insight import InsightResult
from .skill_refine import SkillRefineResult
from .rehearse import RehearsalResult

log = logging.getLogger(__name__)

_DREAMS_DIR = Path.home() / ".nexus" / "vault" / "dreams"


def write_journal(
    *,
    run_id: int,
    depth: str,
    phases_run: list[str],
    consolidation: ConsolidationResult | None = None,
    insight_result: InsightResult | None = None,
    skill_refine_result: SkillRefineResult | None = None,
    rehearsal_result: RehearsalResult | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    duration_ms: int | None = None,
    error: str | None = None,
) -> str:
    _DREAMS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    path = _DREAMS_DIR / f"{today}.md"

    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8")

    entry = _build_entry(
        run_id=run_id,
        depth=depth,
        phases_run=phases_run,
        consolidation=consolidation,
        insight_result=insight_result,
        skill_refine_result=skill_refine_result,
        rehearsal_result=rehearsal_result,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        duration_ms=duration_ms,
        error=error,
    )

    if existing:
        content = existing.rstrip() + "\n\n---\n\n" + entry
    else:
        fm = "---\ntags: [dream-journal, auto-generated]\nkind: dream-journal\n---\n\n"
        content = fm + entry

    path.write_text(content, encoding="utf-8")
    try:
        return str(path.relative_to(Path.home() / ".nexus" / "vault"))
    except ValueError:
        return f"dreams/{today}.md"


def _build_entry(
    *,
    run_id: int,
    depth: str,
    phases_run: list[str],
    consolidation: ConsolidationResult | None,
    insight_result: InsightResult | None,
    skill_refine_result: SkillRefineResult | None,
    rehearsal_result: RehearsalResult | None,
    tokens_in: int,
    tokens_out: int,
    duration_ms: int | None,
    error: str | None,
) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"## Dream Run #{run_id} — {now}", ""]
    lines.append(f"- **Depth:** {depth}")
    lines.append(f"- **Phases:** {', '.join(phases_run) or 'none'}")
    if duration_ms is not None:
        lines.append(f"- **Duration:** {duration_ms / 1000:.1f}s")
    lines.append(f"- **Tokens:** {tokens_in} in / {tokens_out} out")
    lines.append("")

    if error:
        lines.append(f"**Error:** {error}")
        lines.append("")
        return "\n".join(lines)

    if consolidation is not None:
        lines.append("### Consolidation")
        lines.append("")
        lines.append(f"- Actions applied: {consolidation.actions_applied}")
        lines.append(f"- Merges: {consolidation.merges}")
        lines.append(f"- Updates: {consolidation.updates}")
        lines.append(f"- Deletes: {consolidation.deletes}")
        if consolidation.flags:
            lines.append("- Flags:")
            for flag in consolidation.flags:
                lines.append(f"  - {flag}")
        if consolidation.errors:
            lines.append("- Errors:")
            for err in consolidation.errors:
                lines.append(f"  - {err}")
        lines.append("")

    if insight_result is not None and insight_result.insights:
        lines.append("### Insights")
        lines.append("")
        for insight in insight_result.insights:
            lines.append(f"- **{insight.title}** ({insight.confidence}): {insight.body}")
        lines.append("")
    elif insight_result is not None and insight_result.errors:
        lines.append("### Insights")
        lines.append("")
        for err in insight_result.errors:
            lines.append(f"- Error: {err}")
        lines.append("")

    if skill_refine_result is not None and skill_refine_result.suggestions:
        lines.append("### Skill Suggestions")
        lines.append("")
        for s in skill_refine_result.suggestions:
            lines.append(f"- **{s.name}**: {s.reason}")
        lines.append("")

    if rehearsal_result is not None and rehearsal_result.scenarios:
        lines.append("### Scenario Rehearsal")
        lines.append("")
        for sc in rehearsal_result.scenarios:
            lines.append(f"- **{sc.title}** ({sc.likelihood}): {sc.precomputed_note[:100]}")
        lines.append("")

    return "\n".join(lines)
