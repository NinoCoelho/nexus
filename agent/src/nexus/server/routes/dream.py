"""Dream system API routes — status, manual trigger, journal, skill suggestions."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

log = logging.getLogger(__name__)

router = APIRouter(prefix="/dream", tags=["dream"])

_DREAMS_DIR = Path.home() / ".nexus" / "vault" / "dreams"
_SUGGESTIONS_DIR = Path.home() / ".nexus" / "vault" / "dreams" / "suggestions"


def _get_store(request: Request) -> Any:
    from ...dream.engine import get_store
    try:
        return get_store()
    except Exception:
        raise HTTPException(status_code=503, detail="dream system not initialised")


def _run_to_dict(r: Any) -> dict[str, Any]:
    return {
        "id": r.id,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "depth": r.depth,
        "phases_run": r.phases_run,
        "status": r.status,
        "tokens_in": r.tokens_in,
        "tokens_out": r.tokens_out,
        "duration_ms": r.duration_ms,
        "memories_merged": r.memories_merged,
        "insights_generated": r.insights_generated,
        "skills_created": r.skills_created,
        "error": r.error,
    }


@router.get("/status")
async def dream_status(request: Request) -> dict[str, Any]:
    store = _get_store(request)
    last = store.last_run()
    runs = store.list_runs(limit=5)
    budget_used = store.budget_used_today()

    cfg = getattr(request.app.state.mutable_state.get("cfg"), "dream", None)
    daily_budget = cfg.daily_token_budget if cfg else 0

    return {
        "enabled": cfg is not None and getattr(cfg, "enabled", False),
        "running": store.is_running(),
        "last_run": _run_to_dict(last) if last else None,
        "recent_runs": [_run_to_dict(r) for r in runs],
        "budget": {
            "used_today": budget_used,
            "daily_limit": daily_budget,
        },
    }


@router.post("/trigger")
async def dream_trigger(request: Request, depth: str = "light") -> dict[str, Any]:
    store = _get_store(request)
    if store.is_running():
        raise HTTPException(status_code=409, detail="a dream is already running")

    agent = request.app.state.agent
    cfg = request.app.state.mutable_state.get("cfg")
    if cfg is None:
        raise HTTPException(status_code=503, detail="no config loaded")

    dream_cfg = getattr(cfg, "dream", None)
    if dream_cfg is None or not getattr(dream_cfg, "enabled", False):
        raise HTTPException(status_code=403, detail="dream system is disabled")

    provider_registry = agent._provider_registry
    dream_model_id = getattr(dream_cfg, "model_id", None) or None

    if dream_model_id and provider_registry:
        try:
            provider, upstream_model = provider_registry.get_for_model(dream_model_id)
        except KeyError:
            log.warning("dream/trigger: model %r not in registry, using default", dream_model_id)
            provider = agent._nexus_provider
            upstream_model = None
    else:
        provider = agent._nexus_provider
        agent_model = getattr(cfg, "agent", None)
        default_model = getattr(agent_model, "default_model", None) if agent_model else None
        if default_model and provider_registry:
            try:
                provider, upstream_model = provider_registry.get_for_model(default_model)
            except KeyError:
                upstream_model = default_model
        else:
            upstream_model = default_model

    from ...dream.engine import run_dream

    tracker = request.app.state.job_tracker
    sessions = request.app.state.sessions
    from ...server.events import SessionEvent
    def _publish_job_event(kind: str, data: dict[str, Any]) -> None:
        sessions.publish("__jobs__", SessionEvent(kind=kind, data=data))

    dream_job_id = tracker.start(
        type="dream",
        label=f"Dream ({depth})",
        publish_fn=_publish_job_event,
    )
    try:
        result = await run_dream(provider=provider, model_id=upstream_model, cfg=cfg, depth=depth)
    except Exception as exc:
        log.exception("dream trigger failed")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        tracker.done(dream_job_id, publish_fn=_publish_job_event)

    return {
        "run_id": result.run_id,
        "depth": result.depth,
        "phases_run": result.phases_run,
        "status": "failed" if result.error else "done",
        "duration_ms": result.duration_ms,
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "error": result.error,
    }


@router.get("/journal")
async def dream_journal_list(request: Request) -> dict[str, Any]:
    if not _DREAMS_DIR.exists():
        return {"entries": []}

    entries = []
    for f in sorted(_DREAMS_DIR.glob("*.md"), reverse=True):
        if f.name == "README.md":
            continue
        try:
            content = f.read_text(encoding="utf-8")
            entries.append({
                "date": f.stem,
                "path": f"dreams/{f.name}",
                "size": len(content),
                "preview": content[:300],
            })
        except Exception:
            continue

    return {"entries": entries}


@router.get("/journal/{date}")
async def dream_journal_detail(date: str, request: Request) -> dict[str, Any]:
    path = _DREAMS_DIR / f"{date}.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail="journal entry not found")
    content = path.read_text(encoding="utf-8")
    return {"date": date, "path": f"dreams/{date}.md", "content": content}


@router.get("/suggestions")
async def dream_suggestions_list(request: Request) -> dict[str, Any]:
    if not _SUGGESTIONS_DIR.exists():
        return {"suggestions": []}

    suggestions = []
    for f in sorted(_SUGGESTIONS_DIR.glob("*.md"), reverse=True):
        try:
            content = f.read_text(encoding="utf-8")
            import re
            name_match = re.search(r"skill_name:\s*(.+)", content)
            name = name_match.group(1).strip() if name_match else f.stem.split("-")[0]
            desc_match = re.search(r"\*\*Description:\*\*\s*(.+)", content)
            description = desc_match.group(1).strip() if desc_match else ""
            suggestions.append({
                "name": name,
                "filename": f.name,
                "description": description,
                "content": content,
            })
        except Exception:
            continue

    return {"suggestions": suggestions}


@router.post("/suggestions/{filename}/accept")
async def dream_suggestion_accept(filename: str, request: Request) -> dict[str, Any]:
    src = _SUGGESTIONS_DIR / filename
    if not src.exists():
        raise HTTPException(status_code=404, detail="suggestion not found")

    content = src.read_text(encoding="utf-8")
    import re
    draft_match = re.search(r"## Draft SKILL\.md\s*\n(.*)", content, re.DOTALL)
    if not draft_match:
        raise HTTPException(status_code=422, detail="no draft content found in suggestion")

    draft = draft_match.group(1).strip()
    name_match = re.search(r"skill_name:\s*(.+)", content)
    skill_name = name_match.group(1).strip() if name_match else filename.split("-")[0]

    skill_dir = Path.home() / ".nexus" / "skills" / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(draft, encoding="utf-8")

    src.unlink()

    return {"ok": True, "skill_name": skill_name}


@router.delete("/suggestions/{filename}")
async def dream_suggestion_dismiss(filename: str, request: Request) -> dict[str, Any]:
    src = _SUGGESTIONS_DIR / filename
    if not src.exists():
        raise HTTPException(status_code=404, detail="suggestion not found")
    src.unlink()
    return {"ok": True}


@router.get("/runs")
async def dream_runs(request: Request, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    store = _get_store(request)
    runs = store.list_runs(limit=limit, offset=offset)
    return {"runs": [_run_to_dict(r) for r in runs], "count": len(runs)}
