"""Calendar trigger driver — scans the vault every minute and dispatches
events whose start time has arrived.

Two firing modes:

1. **Single-shot** (the default). The driver dispatches when the event's start
   arrives and the event transitions ``scheduled → triggered → done``.
   Recurring events use iCal RRULE so the driver fires the next occurrence
   each time the prior one finishes.

2. **Intra-day fire window** (``all_day=true`` + ``fire_from`` + ``fire_to`` +
   ``fire_every_min``). The driver fires the event repeatedly during a local
   time window. Status stays ``scheduled``; ``session_id`` rolls over to the
   most recent run. State (``fired_events``) tracks per-event last-fire so
   we don't double-fire on consecutive ticks.

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

# Width of the "this is due now, fire it" window for single-shot events.
# Anything older than (last_processed - GRACE) is treated as missed.
_GRACE = timedelta(minutes=2)
# Drop fired_events entries older than this on each tick — bounds the state.
_FIRED_RETENTION = timedelta(days=7)
# Hard cap on how long a single dispatcher() call may block the tick. The
# dispatcher itself is intended to return quickly (it spawns the actual
# agent turn as a detached task), so a slow call here points to a vault
# write / SQLite contention / hung downstream service — none of which
# should be allowed to delay the next tick or other due events.
_DISPATCH_TIMEOUT = 30.0


class Driver(HeartbeatDriver):
    async def check(
        self, state: dict[str, Any]
    ) -> tuple[list[HeartbeatEvent], dict[str, Any]]:
        try:
            from nexus import vault_calendar
            from nexus.calendar_runtime import get_dispatcher, get_notifier
        except Exception:
            log.exception("calendar_trigger: import failed; skipping tick")
            return [], state

        dispatcher = get_dispatcher()
        notifier = get_notifier()
        if dispatcher is None:
            return [], state

        now = datetime.now(UTC)
        last_processed = _parse_iso(state.get("last_processed")) or (now - _GRACE)
        fired_events: dict[str, str] = dict(state.get("fired_events") or {})

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
                if ev.status != "scheduled":
                    continue
                if vault_calendar.effective_trigger(ev, cal) != "on_start":
                    continue

                if ev.has_fire_window:
                    await _handle_fire_window(
                        ev, cal, summary.path, now, fired_events, dispatcher,
                        notifier, vault_calendar,
                    )
                    continue

                # Single-shot path
                due = vault_calendar.next_occurrence_after(
                    ev, last_processed - timedelta(seconds=1), tz=cal.timezone
                )
                if due is None:
                    continue
                if due > now:
                    continue

                if due < last_processed - _GRACE:
                    try:
                        vault_calendar.update_event(
                            summary.path, ev.id, {"status": "missed"}
                        )
                    except Exception:
                        log.exception(
                            "calendar_trigger: failed to mark %s as missed", ev.id
                        )
                    continue

                # Branch on prompt: with prompt → run agent. Without → notify only.
                if vault_calendar.effective_prompt(ev, cal):
                    await _dispatch_with_prompt(
                        summary.path, ev, dispatcher, vault_calendar,
                    )
                else:
                    _notify_alert(summary.path, ev, cal, notifier, vault_calendar)

        # Garbage-collect stale fired_events entries.
        cutoff = now - _FIRED_RETENTION
        fired_events = {
            eid: ts for eid, ts in fired_events.items()
            if (_parse_iso(ts) or now) > cutoff
        }

        return [], {
            "last_processed": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fired_events": fired_events,
        }


async def _dispatch_with_prompt(
    path: str,
    ev,  # noqa: ANN001
    dispatcher,  # noqa: ANN001
    vault_calendar,  # noqa: ANN001
) -> None:
    """Dispatch the agent for an event with a configured (or inherited) prompt."""
    try:
        result = await asyncio.wait_for(
            dispatcher(path=path, event_id=ev.id, mode="background"),
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


def _notify_alert(
    path: str,
    ev,  # noqa: ANN001
    cal,  # noqa: ANN001
    notifier,  # noqa: ANN001
    vault_calendar,  # noqa: ANN001
) -> None:
    """Publish a calendar_alert notification (no agent dispatch) and mark
    the event as ``done``."""
    if notifier is not None:
        try:
            notifier({
                "path": path,
                "event_id": ev.id,
                "title": ev.title,
                "body": ev.body,
                "start": ev.start,
                "end": ev.end,
                "calendar_title": cal.title,
                "all_day": ev.all_day,
            })
            log.info("calendar_trigger: alerted for event %s (no prompt)", ev.id)
        except Exception:
            log.exception("calendar_trigger: notifier raised for %s", ev.id)
    try:
        vault_calendar.update_event(path, ev.id, {"status": "done"})
    except Exception:
        log.exception("calendar_trigger: failed to mark %s done after alert", ev.id)


async def _handle_fire_window(
    ev,  # noqa: ANN001
    cal,  # noqa: ANN001
    path: str,
    now: datetime,
    fired_events: dict[str, str],
    dispatcher,  # noqa: ANN001
    notifier,  # noqa: ANN001
    vault_calendar,  # noqa: ANN001
) -> None:
    """Fire a fire-windowed all-day event if today's window is active and
    enough time has passed since the last fire."""
    tz = _zone(cal.timezone)
    today_local = now.astimezone(tz).date()

    # Is the event "active" today? Use RRULE expansion if recurring; otherwise
    # compare against the event's own start date.
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

    # Compute today's UTC window from the local HH:MM strings.
    try:
        from_h, from_m = (int(x) for x in ev.fire_from.split(":"))
        to_h, to_m = (int(x) for x in ev.fire_to.split(":"))
    except Exception:
        return
    window_start = datetime.combine(today_local, time(from_h, from_m), tzinfo=tz).astimezone(UTC)
    window_end = datetime.combine(today_local, time(to_h, to_m), tzinfo=tz).astimezone(UTC)
    if now < window_start or now > window_end:
        return

    # Dedup: respect fire_every_min since the last fire.
    last_fired = _parse_iso(fired_events.get(ev.id))
    if last_fired is not None:
        next_fire = last_fired + timedelta(minutes=ev.fire_every_min or 1)
        if now < next_fire:
            return

    # Branch on prompt: with prompt run agent, without just notify.
    if vault_calendar.effective_prompt(ev, cal):
        try:
            result = await asyncio.wait_for(
                dispatcher(path=path, event_id=ev.id, mode="background"),
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
    else:
        if notifier is not None:
            try:
                notifier({
                    "path": path,
                    "event_id": ev.id,
                    "title": ev.title,
                    "body": ev.body,
                    "start": ev.start,
                    "end": ev.end,
                    "calendar_title": cal.title,
                    "all_day": ev.all_day,
                })
            except Exception:
                log.exception("calendar_trigger: window notifier raised for %s", ev.id)
        fired_events[ev.id] = now.strftime("%Y-%m-%dT%H:%M:%SZ")


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
    """Parse 'YYYY-MM-DD' into midnight UTC."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


def _zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except Exception:
        return ZoneInfo("UTC")
