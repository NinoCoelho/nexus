"""Calendar trigger driver — scans the vault every minute and dispatches
events whose start time has arrived.

An event fires only when its assignee includes ``"agent"``; everything else
that includes ``"user"`` gets an alarm notification. Events can include both
(e.g. ``"agent,user"``).

Two firing modes for agent-assigned events:

1. **Single-shot** (the default). The driver dispatches when the event's start
   arrives and the event transitions ``scheduled → triggered → done``.
   Recurring events use iCal RRULE: the ``status`` field reflects only the
   *last* run, not the next occurrence's eligibility. Subsequent occurrences
   re-fire regardless of status (only ``cancelled`` opts out). Per-occurrence
   dedup is via ``fired_events`` (tracks the last fired due-time per event).

2. **Intra-day fire window** (``all_day=true`` + ``fire_from`` + ``fire_to`` +
   ``fire_every_min``). The driver fires the event repeatedly during a local
   time window. Status stays ``scheduled``; ``session_id`` rolls over to the
   most recent run. State (``fired_events``) tracks per-event last-fire so
   we don't double-fire on consecutive ticks.

For user-assigned events (alarm):

The driver emits a ``calendar_alarm`` SSE event when the alarm time arrives
(``start - remind_before_min``). Alarms persist until acknowledged or snoozed.
Snoozed alarms re-ring when the snooze duration expires.

The driver returns ``events=[]`` and dispatches inline via the vault dispatch
pipeline so it can capture the resulting session id and stamp it on the
markdown atomically.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from loom.heartbeat import HeartbeatDriver, HeartbeatEvent

log = logging.getLogger(__name__)

_GRACE = timedelta(minutes=2)
_FIRED_RETENTION = timedelta(days=7)
_DISPATCH_TIMEOUT = 30.0
_ALARM_GC_AGE = timedelta(days=7)


class Driver(HeartbeatDriver):
    async def check(
        self, state: dict[str, Any]
    ) -> tuple[list[HeartbeatEvent], dict[str, Any]]:
        try:
            from nexus import vault_calendar
            from nexus.calendar_runtime import get_dispatcher, get_notifier, get_alarm_store
        except Exception:
            log.exception("calendar_trigger: import failed; skipping tick")
            return [], state

        dispatcher = get_dispatcher()
        notifier = get_notifier()
        alarm_store = get_alarm_store()

        now = datetime.now(UTC)
        last_processed = _parse_iso(state.get("last_processed")) or (now - _GRACE)
        fired_events: dict[str, str] = dict(state.get("fired_events") or {})
        alarmed_events: dict[str, str] = dict(state.get("alarmed_events") or {})

        try:
            summaries = vault_calendar.list_calendars()
        except Exception:
            log.exception("calendar_trigger: list_calendars failed")
            return [], state

        for summary in summaries:
            try:
                cal = vault_calendar.read_calendar(summary.path)
            except Exception:
                log.exception("calendar_trigger: read failed for %s", summary.path)
                continue

            for ev in list(cal.events):
                if ev.status == "cancelled":
                    continue

                is_agent = ev.is_agent_assigned
                is_user = ev.has_user_alarm

                if not is_agent and not is_user:
                    continue

                trigger = vault_calendar.effective_trigger(ev, cal)

                # --- Agent auto-dispatch path ---
                if is_agent and trigger == "on_start":
                    if ev.has_fire_window:
                        await _handle_fire_window(
                            ev, cal, summary.path, now, fired_events, dispatcher,
                            vault_calendar,
                        )
                    else:
                        await _handle_agent_single_shot(
                            ev, cal, summary.path, now, last_processed,
                            fired_events, dispatcher, vault_calendar,
                        )

                # --- User alarm path ---
                if is_user:
                    await _handle_user_alarm(
                        ev, cal, summary.path, now, alarmed_events,
                        notifier, alarm_store, vault_calendar,
                    )

        # Re-ring snoozed alarms whose snooze period has expired.
        if alarm_store is not None:
            now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            ready = alarm_store.list_snoozed_ready(now_iso)
            for entry in ready:
                try:
                    _cal = vault_calendar.read_calendar(entry.calendar_path) if entry.calendar_path else None
                    _ev = None
                    _cal_title = ""
                    if _cal:
                        _cal_title = _cal.title
                        _found = vault_calendar.find_event(_cal, entry.event_id)
                        if _found:
                            _ev = _found[0]
                    if notifier:
                        _body = _ev.body if _ev else ""
                        _title = _ev.title if _ev else entry.event_id
                        notifier({
                            "type": "calendar_alarm",
                            "event_id": entry.event_id,
                            "title": _title,
                            "body": _body,
                            "start": entry.occurrence_start,
                            "calendar_title": _cal_title,
                            "path": entry.calendar_path or "",
                            "countdown_seconds": 0,
                            "is_overdue": True,
                            "occurrence_start": entry.occurrence_start,
                        })
                    alarm_store.upsert(type(entry)(
                        event_id=entry.event_id,
                        occurrence_start=entry.occurrence_start,
                        status="ringing",
                        snoozed_until=None,
                        created_at=entry.created_at,
                        calendar_path=entry.calendar_path,
                    ))
                except Exception:
                    log.exception("calendar_trigger: re-ring failed for %s", entry.event_id)

        # Garbage-collect stale entries.
        cutoff = now - _FIRED_RETENTION
        fired_events = {
            eid: ts for eid, ts in fired_events.items()
            if (_parse_iso(ts) or now) > cutoff
        }
        alarmed_cutoff = now - _ALARM_GC_AGE
        alarmed_events = {
            eid: ts for eid, ts in alarmed_events.items()
            if (_parse_iso(ts) or now) > alarmed_cutoff
        }

        if alarm_store is not None:
            gc_cutoff = (now - _ALARM_GC_AGE).strftime("%Y-%m-%dT%H:%M:%SZ")
            alarm_store.garbage_collect(gc_cutoff)

        return [], {
            "last_processed": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fired_events": fired_events,
            "alarmed_events": alarmed_events,
        }


async def _handle_agent_single_shot(
    ev, cal, path, now, last_processed, fired_events, dispatcher, vault_calendar,
):
    if not ev.rrule and ev.status != "scheduled":
        return

    due = vault_calendar.next_occurrence_after(
        ev, last_processed - timedelta(seconds=1), tz=cal.timezone
    )
    completed_set = set(ev.completed_occurrences)
    due_iso: str | None = None
    for _ in range(366):
        if due is None:
            break
        candidate = due.strftime("%Y-%m-%dT%H:%M:%SZ")
        if candidate not in completed_set:
            due_iso = candidate
            break
        due = vault_calendar.next_occurrence_after(ev, due, tz=cal.timezone)
    if due is None or due_iso is None:
        return
    if due > now:
        return

    if ev.rrule:
        last_fired = _parse_iso(fired_events.get(ev.id))
        if last_fired is not None and due <= last_fired:
            return

    if due < last_processed - _GRACE:
        if ev.rrule:
            return
        try:
            vault_calendar.update_event(path, ev.id, {"status": "missed"})
        except Exception:
            log.exception("calendar_trigger: failed to mark %s as missed", ev.id)
        return

    await _dispatch_event(path, ev, dispatcher, vault_calendar, occurrence_start=due_iso)
    if ev.rrule:
        fired_events[ev.id] = due_iso


async def _handle_user_alarm(
    ev, cal, path, now, alarmed_events, notifier, alarm_store, vault_calendar,
):
    if ev.status == "cancelled":
        return

    lead = ev.alarm_lead_seconds
    alarm_time_offset = timedelta(seconds=lead)

    due = vault_calendar.next_occurrence_after(
        ev, now - alarm_time_offset - _GRACE, tz=cal.timezone
    )
    completed_set = set(ev.completed_occurrences)
    due_iso: str | None = None
    due_dt: datetime | None = None
    for _ in range(366):
        if due is None:
            break
        candidate = due.strftime("%Y-%m-%dT%H:%M:%SZ")
        if candidate not in completed_set:
            due_iso = candidate
            due_dt = due
            break
        due = vault_calendar.next_occurrence_after(ev, due, tz=cal.timezone)

    if due_dt is None or due_iso is None:
        if not ev.rrule and ev.start:
            parsed = _parse_iso(ev.start)
            if parsed:
                due_dt = parsed
                due_iso = ev.start
            else:
                parsed_date = _parse_date(ev.start)
                if parsed_date:
                    due_dt = parsed_date
                    due_iso = ev.start
        if due_dt is None:
            return

    alarm_time = due_dt - alarm_time_offset

    if now < alarm_time:
        return

    # Dedup: already alarmed for this occurrence?
    last_alarmed = _parse_iso(alarmed_events.get(ev.id))
    if last_alarmed is not None and due_dt <= last_alarmed:
        return

    # Check alarm store — skip if already acknowledged or still snoozed.
    if alarm_store is not None:
        existing = alarm_store.get(ev.id, due_iso)
        if existing is not None:
            if existing.status == "acknowledged":
                return
            if existing.status == "snoozed" and existing.snoozed_until:
                snooze_dt = _parse_iso(existing.snoozed_until)
                if snooze_dt and now < snooze_dt:
                    return

    is_overdue = now > due_dt
    countdown = max(0, int((due_dt - now).total_seconds()))

    if notifier:
        notifier({
            "type": "calendar_alarm",
            "event_id": ev.id,
            "title": ev.title,
            "body": ev.body,
            "start": ev.start,
            "calendar_title": cal.title,
            "path": path,
            "countdown_seconds": countdown,
            "is_overdue": is_overdue,
            "occurrence_start": due_iso,
        })

    if alarm_store is not None:
        from nexus.alarm_store import AlarmEntry
        alarm_store.upsert(AlarmEntry(
            event_id=ev.id,
            occurrence_start=due_iso,
            status="ringing",
            calendar_path=path,
        ))

    alarmed_events[ev.id] = due_iso


async def _dispatch_event(
    path: str,
    ev,
    dispatcher,
    vault_calendar,
    *,
    occurrence_start: str | None = None,
) -> None:
    try:
        result = await asyncio.wait_for(
            dispatcher(
                path=path, event_id=ev.id, mode="background",
                occurrence_start=occurrence_start,
            ),
            timeout=_DISPATCH_TIMEOUT,
        )
        log.info(
            "calendar_trigger: dispatched event %s (session=%s)",
            ev.id, result.get("session_id"),
        )
    except asyncio.TimeoutError:
        log.error(
            "calendar_trigger: dispatch timed out after %.0fs for %s",
            _DISPATCH_TIMEOUT, ev.id,
        )
        try:
            vault_calendar.update_event(path, ev.id, {"status": "failed"})
        except Exception:
            log.exception("calendar_trigger: also failed to mark %s as failed", ev.id)
    except Exception:
        log.exception("calendar_trigger: dispatch failed for %s", ev.id)
        try:
            vault_calendar.update_event(path, ev.id, {"status": "failed"})
        except Exception:
            log.exception("calendar_trigger: also failed to mark %s as failed", ev.id)


async def _handle_fire_window(
    ev,
    cal,
    path: str,
    now: datetime,
    fired_events: dict[str, str],
    dispatcher,
    vault_calendar,
) -> None:
    tz = _zone(cal.timezone)
    today_local = now.astimezone(tz).date()

    if ev.rrule:
        from nexus.vault_calendar.recurrence import expand_window
        win_start_utc = datetime.combine(today_local, time(0, 0), tzinfo=tz).astimezone(UTC)
        win_end_utc = win_start_utc + timedelta(days=1)
        if not expand_window(ev, win_start_utc, win_end_utc, tz=cal.timezone):
            return
    else:
        ev_start = _parse_iso(ev.start) or _parse_date(ev.start)
        if ev_start is None:
            return
        ev_date = ev_start.astimezone(tz).date() if ev_start.tzinfo else ev_start.date()
        if ev_date != today_local:
            return

    try:
        from_h, from_m = (int(x) for x in ev.fire_from.split(":"))
        to_h, to_m = (int(x) for x in ev.fire_to.split(":"))
    except Exception:
        return
    window_start = datetime.combine(today_local, time(from_h, from_m), tzinfo=tz).astimezone(UTC)
    window_end = datetime.combine(today_local, time(to_h, to_m), tzinfo=tz).astimezone(UTC)
    if now < window_start:
        return
    if window_end > window_start and now > window_end:
        return

    last_fired = _parse_iso(fired_events.get(ev.id))
    if last_fired is not None:
        next_fire = last_fired + timedelta(minutes=ev.fire_every_min or 1)
        if now < next_fire:
            return

    try:
        result = await asyncio.wait_for(
            dispatcher(
                path=path, event_id=ev.id, mode="background",
                occurrence_start=None,
            ),
            timeout=_DISPATCH_TIMEOUT,
        )
        log.info(
            "calendar_trigger: fired window event %s (session=%s)",
            ev.id, result.get("session_id"),
        )
        fired_events[ev.id] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    except asyncio.TimeoutError:
        log.error(
            "calendar_trigger: window dispatch timed out after %.0fs for %s",
            _DISPATCH_TIMEOUT, ev.id,
        )
    except Exception:
        log.exception("calendar_trigger: window dispatch failed for %s", ev.id)


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_date(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


def _zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except Exception:
        return ZoneInfo("UTC")
