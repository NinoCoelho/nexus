"""Event CRUD on vault calendars."""

from __future__ import annotations

import uuid
from typing import Any

from .calendars import read_calendar, write_calendar
from .models import EVENT_STATUSES, EVENT_TRIGGERS, Calendar, Event
from .recurrence import add_duration


def find_event(cal: Calendar, event_id: str) -> tuple[Event, int] | None:
    for idx, ev in enumerate(cal.events):
        if ev.id == event_id:
            return ev, idx
    return None


def effective_trigger(event: Event, cal: Calendar) -> str:
    """Resolve the effective trigger mode for an event ('on_start' or 'off')."""
    if event.trigger in EVENT_TRIGGERS:
        return event.trigger
    return "on_start" if cal.auto_trigger else "off"


def effective_model(event: Event, cal: Calendar) -> str | None:
    """Per-event model override → calendar default → agent default (None)."""
    if event.model and event.model.strip():
        return event.model.strip()
    if cal.default_model:
        return cal.default_model
    return None


_HHMM_RE = __import__("re").compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _validate_hhmm(value: str | None, field: str) -> None:
    if value is None or value == "":
        return
    if not _HHMM_RE.match(value):
        raise ValueError(f"{field} must be 'HH:MM' (24-hour); got {value!r}")


def _validate_fire_every(value: int | None) -> None:
    if value is None:
        return
    if not isinstance(value, int) or value < 1 or value > 1440:
        raise ValueError(f"fire_every_min must be 1..1440; got {value!r}")


def add_event(
    path: str,
    *,
    title: str,
    start: str,
    end: str | None = None,
    body: str = "",
    trigger: str | None = None,
    rrule: str | None = None,
    all_day: bool = False,
    status: str = "scheduled",
    fire_from: str | None = None,
    fire_to: str | None = None,
    fire_every_min: int | None = None,
    model: str | None = None,
    assignee: str | None = None,
) -> Event:
    if status not in EVENT_STATUSES:
        raise ValueError(f"invalid status {status!r}; allowed: {sorted(EVENT_STATUSES)}")
    if trigger is not None and trigger not in EVENT_TRIGGERS:
        raise ValueError(f"invalid trigger {trigger!r}; allowed: {sorted(EVENT_TRIGGERS)}")
    _validate_hhmm(fire_from, "fire_from")
    _validate_hhmm(fire_to, "fire_to")
    _validate_fire_every(fire_every_min)
    cal = read_calendar(path)
    event = Event(
        id=str(uuid.uuid4()),
        title=title,
        start=start,
        end=end,
        body=body,
        status=status,
        trigger=trigger,
        rrule=rrule,
        all_day=all_day,
        fire_from=fire_from,
        fire_to=fire_to,
        fire_every_min=fire_every_min,
        model=(model.strip() if isinstance(model, str) and model.strip() else None),
        assignee=(assignee.strip() if isinstance(assignee, str) and assignee.strip() else None),
    )
    cal.events.append(event)
    write_calendar(path, cal)
    return event


def update_event(path: str, event_id: str, updates: dict[str, Any]) -> Event:
    cal = read_calendar(path)
    found = find_event(cal, event_id)
    if found is None:
        raise KeyError(f"event {event_id!r} not found")
    ev, _ = found
    if "title" in updates:
        ev.title = str(updates["title"])
    if "body" in updates:
        ev.body = str(updates["body"])
    if "start" in updates:
        ev.start = str(updates["start"])
    if "end" in updates:
        v = updates["end"]
        ev.end = str(v) if v else None
    if "session_id" in updates:
        v = updates["session_id"]
        ev.session_id = str(v) if v else None
    if "status" in updates:
        v = updates["status"]
        if v in EVENT_STATUSES:
            ev.status = v
        else:
            raise ValueError(f"invalid status {v!r}; allowed: {sorted(EVENT_STATUSES)}")
    if "trigger" in updates:
        v = updates["trigger"]
        if v in (None, ""):
            ev.trigger = None
        elif v in EVENT_TRIGGERS:
            ev.trigger = v
        else:
            raise ValueError(f"invalid trigger {v!r}; allowed: {sorted(EVENT_TRIGGERS)}")
    if "rrule" in updates:
        v = updates["rrule"]
        ev.rrule = str(v) if v else None
    if "all_day" in updates:
        ev.all_day = bool(updates["all_day"])
    if "fire_from" in updates:
        v = updates["fire_from"]
        _validate_hhmm(v if isinstance(v, str) and v else None, "fire_from")
        ev.fire_from = v if v else None
    if "fire_to" in updates:
        v = updates["fire_to"]
        _validate_hhmm(v if isinstance(v, str) and v else None, "fire_to")
        ev.fire_to = v if v else None
    if "fire_every_min" in updates:
        v = updates["fire_every_min"]
        if v in (None, "", 0):
            ev.fire_every_min = None
        else:
            iv = int(v)
            _validate_fire_every(iv)
            ev.fire_every_min = iv
    if "model" in updates:
        v = updates["model"]
        ev.model = str(v).strip() if v and str(v).strip() else None
    if "assignee" in updates:
        v = updates["assignee"]
        ev.assignee = str(v).strip() if v and str(v).strip() else None
    write_calendar(path, cal)
    return ev


def move_event(
    path: str,
    event_id: str,
    new_start: str,
    new_end: str | None = None,
) -> Event:
    """Reschedule an event. If ``new_end`` is omitted, preserve duration."""
    cal = read_calendar(path)
    found = find_event(cal, event_id)
    if found is None:
        raise KeyError(f"event {event_id!r} not found")
    ev, _ = found
    if new_end is None and ev.end:
        new_end = add_duration(ev.start, ev.end, new_start)
    ev.start = new_start
    if new_end is not None:
        ev.end = new_end
    write_calendar(path, cal)
    return ev


def delete_event(path: str, event_id: str) -> None:
    cal = read_calendar(path)
    found = find_event(cal, event_id)
    if found is None:
        raise KeyError(f"event {event_id!r} not found")
    _, idx = found
    cal.events.pop(idx)
    write_calendar(path, cal)


def fire_event(path: str, event_id: str, *, session_id: str) -> Event:
    """Mark an event as triggered with a linked session id (atomic write)."""
    return update_event(path, event_id, {"status": "triggered", "session_id": session_id})
