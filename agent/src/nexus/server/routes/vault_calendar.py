"""Routes for vault calendar operations: /vault/calendar*."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..deps import get_agent, get_sessions
from ..session_store import SessionStore

router = APIRouter()


# ── calendar-level endpoints ─────────────────────────────────────────────────


@router.get("/vault/calendar")
async def vault_calendar_get(path: str) -> dict:
    from ... import vault_calendar
    try:
        cal = vault_calendar.read_calendar(path)
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file not found")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return {"path": path, **cal.to_dict()}


@router.get("/vault/calendar/list")
async def vault_calendar_list() -> dict:
    """Return summaries for every calendar file in the vault."""
    from ... import vault_calendar
    items = [s.to_dict() for s in vault_calendar.list_calendars()]
    return {"calendars": items, "count": len(items)}


@router.post("/vault/calendar", status_code=status.HTTP_201_CREATED)
async def vault_calendar_create(body: dict) -> dict:
    """Scaffold a new calendar .md file. Body: {path, title?, timezone?, prompt?}."""
    from ... import vault_calendar
    path = body.get("path", "")
    if not path:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`path` required")
    try:
        cal = vault_calendar.create_empty(
            path,
            title=body.get("title"),
            timezone=body.get("timezone"),
            prompt=body.get("prompt"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return {"path": path, **cal.to_dict()}


@router.patch("/vault/calendar")
async def vault_calendar_patch(body: dict, path: str) -> dict:
    """Update calendar-level metadata. Body: {title?, prompt?, timezone?, auto_trigger?, default_duration_min?}."""
    from ... import vault_calendar
    try:
        cal = vault_calendar.update_calendar(path, body)
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file not found")
    return {"path": path, **cal.to_dict()}


# ── event endpoints ──────────────────────────────────────────────────────────


@router.get("/vault/calendar/events")
async def vault_calendar_events(
    path: Optional[str] = None,
    from_utc: Optional[str] = Query(None, alias="from"),
    to_utc: Optional[str] = Query(None, alias="to"),
    status_filter: Optional[str] = Query(None, alias="status"),
) -> dict:
    """Range query — expands RRULE inside the [from, to] window.

    All params optional. If ``path`` is omitted, queries every calendar.
    """
    from ... import vault_calendar
    hits = vault_calendar.query_events(
        from_utc=from_utc, to_utc=to_utc, status=status_filter, calendar_path=path,
    )
    return {"events": hits, "count": len(hits)}


@router.post("/vault/calendar/events", status_code=status.HTTP_201_CREATED)
async def vault_calendar_add_event(body: dict, path: str) -> dict:
    from ... import vault_calendar
    title = body.get("title", "")
    start = body.get("start", "")
    if not title or not start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`title` and `start` are required",
        )
    try:
        ev = vault_calendar.add_event(
            path,
            title=title,
            start=start,
            end=body.get("end"),
            body=body.get("body", ""),
            trigger=body.get("trigger"),
            rrule=body.get("rrule"),
            all_day=bool(body.get("all_day", False)),
            status=body.get("status", "scheduled"),
            fire_from=body.get("fire_from"),
            fire_to=body.get("fire_to"),
            fire_every_min=body.get("fire_every_min"),
            model=body.get("model"),
            assignee=body.get("assignee"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file not found")
    return ev.to_dict()


@router.patch("/vault/calendar/events/{event_id}")
async def vault_calendar_patch_event(event_id: str, body: dict, path: str) -> dict:
    """Update event fields or reschedule. Body keys: title, body, start, end,
    status, trigger, rrule, all_day, session_id, completed_occurrences (full
    list replace), complete_occurrence (append one occurrence_start to the
    completed list — used by the UI to mark a single instance of a recurring
    event done without touching the parent ``status``), uncomplete_occurrence
    (remove one)."""
    from ... import vault_calendar
    try:
        if "start" in body and len(body) <= 2 and ("end" in body or len(body) == 1):
            # Drag-style move: only start (and maybe end). Preserve duration if end omitted.
            ev = vault_calendar.move_event(path, event_id, body["start"], body.get("end"))
            # Re-apply any other fields if present.
            extras = {k: v for k, v in body.items() if k not in ("start", "end")}
            if extras:
                ev = vault_calendar.update_event(path, event_id, extras)
        else:
            ev = vault_calendar.update_event(path, event_id, body)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return ev.to_dict()


@router.delete("/vault/calendar/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def vault_calendar_delete_event(event_id: str, path: str) -> None:
    from ... import vault_calendar
    try:
        vault_calendar.delete_event(path, event_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post("/vault/calendar/events/{event_id}/fire")
async def vault_calendar_fire_event(
    event_id: str,
    path: str,
    a=Depends(get_agent),
    store: SessionStore = Depends(get_sessions),
) -> dict:
    """Manually fire a missed/scheduled event in background mode. Returns the new session id."""
    from ... import vault_calendar
    from .vault_dispatch import _dispatch_impl
    try:
        cal = vault_calendar.read_calendar(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    found = vault_calendar.find_event(cal, event_id)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="event not found")
    if not found[0].is_agent_assigned:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="event is not assigned to the agent",
        )
    try:
        return await _dispatch_impl(
            path=path, card_id=None, event_id=event_id,
            mode="background", a=a, store=store,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.post("/vault/calendar/query")
async def vault_calendar_query(body: dict) -> dict:
    """Cross-calendar event search. Body keys (all optional):
    from, to, status, calendar_path, limit. Time fields are ISO-8601 UTC.
    """
    from ... import vault_calendar
    hits = vault_calendar.query_events(
        from_utc=body.get("from") or body.get("from_utc"),
        to_utc=body.get("to") or body.get("to_utc"),
        status=body.get("status"),
        calendar_path=body.get("calendar_path"),
        limit=int(body.get("limit") or 500),
    )
    return {"events": hits, "count": len(hits)}
