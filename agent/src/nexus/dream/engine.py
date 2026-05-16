"""Dream engine — orchestrates the dream cycle phases."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .consolidate import ConsolidationResult, run_consolidation
from .insight import InsightResult, run_insight_extraction
from .journal import write_journal
from .rehearse import RehearsalResult, run_scenario_rehearsal
from .skill_refine import SkillRefineResult, run_skill_refinement
from .state import DreamStateStore

log = logging.getLogger(__name__)

_DB_PATH = Path.home() / ".nexus" / "dream_state.sqlite"

_store: DreamStateStore | None = None


def get_store() -> DreamStateStore:
    global _store
    if _store is None:
        _store = DreamStateStore(_DB_PATH)
    return _store


def close_store() -> None:
    global _store
    if _store is not None:
        _store.close()
        _store = None


@dataclass
class DreamResult:
    run_id: int
    depth: str
    phases_run: list[str]
    consolidation: ConsolidationResult | None
    insights: InsightResult | None
    skill_refine: SkillRefineResult | None
    rehearsal: RehearsalResult | None
    tokens_in: int
    tokens_out: int
    duration_ms: int
    error: str | None = None


def _publish_event(event_type: str, data: dict[str, Any] | None = None) -> None:
    try:
        from ..server.event_bus import publish
        publish({"type": event_type, **(data or {})})
    except Exception:
        pass


async def run_dream(
    *,
    provider: Any,
    model_id: str | None = None,
    cfg: Any = None,
    depth: str = "light",
) -> DreamResult:
    store = get_store()

    if store.is_running():
        log.warning("dream/engine: a dream is already running, skipping")
        return DreamResult(
            run_id=0, depth=depth, phases_run=[], consolidation=None,
            insights=None, skill_refine=None, rehearsal=None,
            tokens_in=0, tokens_out=0, duration_ms=0,
            error="dream already running",
        )

    if cfg is None:
        try:
            from ..config_file import load
            cfg = load()
        except Exception:
            log.exception("dream/engine: failed to load config")
            return DreamResult(
                run_id=0, depth=depth, phases_run=[], consolidation=None,
                insights=None, skill_refine=None, rehearsal=None,
                tokens_in=0, tokens_out=0, duration_ms=0,
                error="config load failed",
            )

    dream_cfg = getattr(cfg, "dream", None)
    if dream_cfg is None:
        return DreamResult(
            run_id=0, depth=depth, phases_run=[], consolidation=None,
            insights=None, skill_refine=None, rehearsal=None,
            tokens_in=0, tokens_out=0, duration_ms=0,
            error="no dream config",
        )

    max_tokens = dream_cfg.max_output_tokens
    context_budget = dream_cfg.context_budget_tokens
    max_duration = dream_cfg.max_duration_seconds
    daily_budget = dream_cfg.daily_token_budget

    budget_used = store.budget_used_today()
    if budget_used >= daily_budget:
        log.info("dream/engine: daily budget exhausted (%d/%d)", budget_used, daily_budget)
        return DreamResult(
            run_id=0, depth=depth, phases_run=[], consolidation=None,
            insights=None, skill_refine=None, rehearsal=None,
            tokens_in=0, tokens_out=0, duration_ms=0,
            error="daily budget exhausted",
        )

    resolved_model = model_id or dream_cfg.model_id or None

    phases: list[str] = ["consolidation"]

    if depth in ("medium", "deep") and (daily_budget - budget_used) > daily_budget * 0.3:
        phases.append("insight")

    if depth == "deep" and (daily_budget - budget_used) > daily_budget * 0.6:
        phases.append("skill_refine")
        phases.append("rehearsal")

    run_id = store.start_run(depth=depth, phases=",".join(phases))
    t0 = asyncio.get_event_loop().time()

    _publish_event("dream.started", {
        "run_id": run_id, "depth": depth, "phases": phases,
    })
    log.info("dream/engine: starting run #%d (depth=%s, phases=%s)", run_id, depth, phases)

    consolidation_result: ConsolidationResult | None = None
    insight_result: InsightResult | None = None
    skill_refine_result: SkillRefineResult | None = None
    rehearsal_result: RehearsalResult | None = None
    tokens_in = 0
    tokens_out = 0
    error: str | None = None

    try:
        if "consolidation" in phases:
            consolidation_result = await asyncio.wait_for(
                run_consolidation(
                    provider=provider,
                    model_id=resolved_model,
                    max_tokens=max_tokens,
                    context_budget=context_budget,
                ),
                timeout=float(max_duration),
            )
            tokens_in += consolidation_result.tokens_in
            tokens_out += consolidation_result.tokens_out

            _publish_event("dream.phase_completed", {
                "run_id": run_id, "phase": "consolidation",
                "merged": consolidation_result.merges,
                "updated": consolidation_result.updates,
                "deleted": consolidation_result.deletes,
            })

        if "insight" in phases and not error:
            last_run = store.last_run()
            since = last_run.started_at if last_run else None

            insight_result = await run_insight_extraction(
                provider=provider,
                state_store=store,
                model_id=resolved_model,
                max_tokens=max_tokens,
                context_budget=context_budget,
                since=since,
            )
            tokens_in += insight_result.tokens_in
            tokens_out += insight_result.tokens_out

            _publish_event("dream.phase_completed", {
                "run_id": run_id, "phase": "insight",
                "insights_generated": len(insight_result.insights),
            })

            for insight in insight_result.insights:
                _publish_event("dream.insight", {
                    "run_id": run_id,
                    "title": insight.title,
                    "confidence": insight.confidence,
                })

        if "skill_refine" in phases and not error:
            skill_refine_result = await run_skill_refinement(
                provider=provider,
                state_store=store,
                model_id=resolved_model,
                max_tokens=max_tokens,
                context_budget=context_budget,
            )

            tokens_in += skill_refine_result.tokens_in
            tokens_out += skill_refine_result.tokens_out

            _publish_event("dream.phase_completed", {
                "run_id": run_id, "phase": "skill_refine",
                "suggestions": len(skill_refine_result.suggestions),
            })

        if "rehearsal" in phases and not error:
            rehearsal_result = await run_scenario_rehearsal(
                provider=provider,
                state_store=store,
                model_id=resolved_model,
                max_tokens=max_tokens,
                context_budget=context_budget,
            )
            tokens_in += rehearsal_result.tokens_in
            tokens_out += rehearsal_result.tokens_out

            _publish_event("dream.phase_completed", {
                "run_id": run_id, "phase": "rehearsal",
                "scenarios": len(rehearsal_result.scenarios),
            })

    except asyncio.TimeoutError:
        error = f"timed out after {max_duration}s"
        log.error("dream/engine: run #%d timed out", run_id)
    except Exception as exc:
        error = str(exc)
        log.exception("dream/engine: run #%d failed", run_id)

    elapsed = asyncio.get_event_loop().time() - t0
    duration_ms = int(elapsed * 1000)

    store.finish_run(
        run_id,
        status="failed" if error else "done",
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        duration_ms=duration_ms,
        memories_merged=consolidation_result.merges if consolidation_result else 0,
        insights_generated=len(insight_result.insights) if insight_result else 0,
        skills_created=0,
        error=error,
    )

    if tokens_in + tokens_out > 0:
        store.add_budget_spend(tokens_in + tokens_out)

    try:
        journal_path = write_journal(
            run_id=run_id,
            depth=depth,
            phases_run=phases,
            consolidation=consolidation_result,
            insight_result=insight_result,
            skill_refine_result=skill_refine_result,
            rehearsal_result=rehearsal_result,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            duration_ms=duration_ms,
            error=error,
        )
        log.info("dream/engine: journal written to vault/%s", journal_path)
    except Exception:
        log.exception("dream/engine: journal write failed")

    _publish_event("dream.completed", {
        "run_id": run_id,
        "depth": depth,
        "status": "failed" if error else "done",
        "duration_ms": duration_ms,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "phases": phases,
    })

    log.info(
        "dream/engine: run #%d complete (status=%s, duration=%.1fs, phases=%s)",
        run_id, "failed" if error else "done", elapsed, phases,
    )

    return DreamResult(
        run_id=run_id,
        depth=depth,
        phases_run=phases,
        consolidation=consolidation_result,
        insights=insight_result,
        skill_refine=skill_refine_result,
        rehearsal=rehearsal_result,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        duration_ms=duration_ms,
        error=error,
    )
