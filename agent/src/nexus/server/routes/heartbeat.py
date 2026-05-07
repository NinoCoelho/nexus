"""Heartbeat dashboard API routes.

Provides read-only and control endpoints for the heartbeat scheduler,
registered heartbeats, agent-assigned calendar events, and the fire log.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from loom.heartbeat import HeartbeatManager, parse_schedule
from loom.heartbeat.cron import is_due

log = logging.getLogger(__name__)

router = APIRouter(prefix="/heartbeat", tags=["heartbeat"])


def _get_state(request: Request) -> tuple[Any, Any, Any, Any]:
    scheduler = getattr(request.app.state, "heartbeat_scheduler", None)
    hb_registry = getattr(request.app.state, "heartbeat_registry", None)
    hb_store = getattr(request.app.state, "heartbeat_store", None)
    hb_log_store = getattr(request.app.state, "heartbeat_log_store", None)
    if hb_registry is None:
        raise HTTPException(status_code=503, detail="heartbeat system not initialised")
    return scheduler, hb_registry, hb_store, hb_log_store


def _health(run: Any) -> str:
    if run is None:
        return "unknown"
    if run.last_error:
        return "error"
    if run.last_fired is None:
        return "idle"
    return "healthy"


def _next_due(schedule_str: str, run: Any) -> str | None:
    try:
        sched = parse_schedule(schedule_str)
    except ValueError:
        return None
    now = datetime.now(UTC)
    last = run.last_check if run else None
    if is_due(sched, last, now):
        return "now"
    if sched.is_interval and sched.interval_seconds and last:
        nd = datetime.fromtimestamp(
            last.timestamp() + sched.interval_seconds, tz=UTC
        )
        return nd.isoformat()
    return None


@router.get("")
async def heartbeat_list(request: Request) -> dict:
    scheduler, hb_registry, hb_store, _ = _get_state(request)
    records = hb_registry.list()
    runs_map: dict[str, Any] = {}
    if hb_store:
        for r in hb_store.list_runs():
            runs_map[r.heartbeat_id] = r
    heartbeats = []
    for rec in records:
        run = runs_map.get(rec.id)
        heartbeats.append({
            "id": rec.id,
            "name": rec.name,
            "description": rec.description,
            "schedule": rec.schedule,
            "enabled": rec.enabled,
            "instructions": rec.instructions,
            "health": _health(run),
            "last_check": run.last_check.isoformat() if run and run.last_check else None,
            "last_fired": run.last_fired.isoformat() if run and run.last_fired else None,
            "last_error": run.last_error if run else None,
            "next_due": _next_due(rec.schedule, run),
            "state": run.state if run else {},
        })
    return {
        "heartbeats": heartbeats,
        "scheduler_running": scheduler.running if scheduler else False,
        "tick_interval": scheduler._tick_interval if scheduler else None,
    }


@router.get("/events")
async def heartbeat_events(request: Request) -> dict:
    _, _, _, _ = _get_state(request)
    try:
        from ... import vault_calendar
    except ImportError:
        raise HTTPException(status_code=503, detail="vault_calendar not available")

    summaries = vault_calendar.list_calendars()
    events: list[dict[str, Any]] = []
    for summary in summaries:
        try:
            cal = vault_calendar.read_calendar(summary.path)
        except Exception:
            continue
        for ev in cal.events:
            if not ev.is_agent_assigned:
                continue
            events.append({
                "event_id": ev.id,
                "title": ev.title,
                "start": ev.start,
                "end": ev.end,
                "status": ev.status,
                "session_id": ev.session_id,
                "rrule": ev.rrule,
                "trigger": vault_calendar.effective_trigger(ev, cal),
                "all_day": ev.all_day,
                "fire_from": ev.fire_from,
                "fire_to": ev.fire_to,
                "fire_every_min": ev.fire_every_min,
                "assignee": ev.assignee,
                "model": ev.model,
                "calendar_path": summary.path,
                "calendar_title": cal.title,
                "calendar_tz": cal.timezone,
                "body": ev.body,
            })
    return {"events": events, "count": len(events)}


@router.get("/{heartbeat_id}")
async def heartbeat_detail(heartbeat_id: str, request: Request) -> dict:
    _, hb_registry, hb_store, hb_log_store = _get_state(request)
    record = hb_registry.get(heartbeat_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"heartbeat {heartbeat_id!r} not found")
    run = hb_store.get_run(heartbeat_id) if hb_store else None
    log_entries = []
    if hb_log_store:
        log_entries = [
            {
                "id": e.id,
                "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                "event_id": e.event_id,
                "event_title": e.event_title,
                "calendar_path": e.calendar_path,
                "session_id": e.session_id,
                "status": e.status,
                "error": e.error,
                "duration_ms": e.duration_ms,
            }
            for e in hb_log_store.list_log(heartbeat_id=heartbeat_id, limit=20)
        ]
    return {
        "id": record.id,
        "name": record.name,
        "description": record.description,
        "schedule": record.schedule,
        "enabled": record.enabled,
        "instructions": record.instructions,
        "source_dir": str(record.source_dir),
        "health": _health(run),
        "last_check": run.last_check.isoformat() if run and run.last_check else None,
        "last_fired": run.last_fired.isoformat() if run and run.last_fired else None,
        "last_error": run.last_error if run else None,
        "next_due": _next_due(record.schedule, run),
        "state": run.state if run else {},
        "log": log_entries,
    }


@router.patch("/{heartbeat_id}")
async def heartbeat_patch(heartbeat_id: str, body: dict, request: Request) -> dict:
    _, hb_registry, hb_store, _ = _get_state(request)
    manager = HeartbeatManager(hb_registry, hb_store)
    enabled = body.get("enabled")
    if enabled is None:
        raise HTTPException(status_code=422, detail="'enabled' is required")
    action = "enable" if enabled else "disable"
    result = manager.invoke({"action": action, "name": heartbeat_id})
    if result.startswith("error:"):
        raise HTTPException(status_code=422, detail=result)
    return {"ok": True, "message": result}


@router.post("/{heartbeat_id}/trigger")
async def heartbeat_trigger(heartbeat_id: str, request: Request) -> dict:
    scheduler, _, _, _ = _get_state(request)
    if scheduler is None:
        raise HTTPException(status_code=503, detail="scheduler not available")
    try:
        turns = await scheduler.trigger(heartbeat_id)
        return {"ok": True, "turns": len(turns)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/reload")
async def heartbeat_reload(request: Request) -> dict:
    _, hb_registry, _, _ = _get_state(request)
    hb_registry.reload()
    count = len(hb_registry.list())
    return {"ok": True, "heartbeats": count}


@router.get("/{heartbeat_id}/log")
async def heartbeat_log(heartbeat_id: str, request: Request, limit: int = 50, offset: int = 0) -> dict:
    _, _, _, hb_log_store = _get_state(request)
    if hb_log_store is None:
        return {"entries": [], "count": 0}
    entries = hb_log_store.list_log(heartbeat_id=heartbeat_id, limit=limit, offset=offset)
    return {
        "entries": [
            {
                "id": e.id,
                "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                "event_id": e.event_id,
                "event_title": e.event_title,
                "calendar_path": e.calendar_path,
                "session_id": e.session_id,
                "status": e.status,
                "error": e.error,
                "duration_ms": e.duration_ms,
            }
            for e in entries
        ],
        "count": len(entries),
    }
