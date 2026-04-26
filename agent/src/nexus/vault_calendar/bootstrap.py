"""Calendar bootstrap helpers — runs at server startup.

* :func:`ensure_default_calendar` — if the vault has no calendar files at
  startup, create ``Calendars/Default.md`` so the UI dropdown isn't empty.
* :func:`sweep_missed` — synchronous catch-up: any event with a past start
  time and ``status=scheduled`` is reclassified ``missed`` so the user can
  fire it manually from the UI.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from .calendars import create_empty, list_calendars, read_calendar, write_calendar
from .events import effective_trigger
from .recurrence import _parse_iso, next_occurrence_after

log = logging.getLogger(__name__)

DEFAULT_CALENDAR_PATH = "Calendars/Default.md"
DEFAULT_PROMPT = (
    "You are the assistant for the Default calendar. When a calendar event "
    "is dispatched to you, read its title and body, propose 2-3 concrete "
    "actions to help the user with that event, then wait for instructions."
)


def ensure_default_calendar() -> str | None:
    """Create the default calendar if no calendars exist. Returns the path created, or None."""
    try:
        if list_calendars():
            return None
    except Exception:
        log.exception("ensure_default_calendar: list_calendars failed")
        return None
    try:
        create_empty(
            DEFAULT_CALENDAR_PATH,
            title="Default",
            timezone="UTC",
            prompt=DEFAULT_PROMPT,
        )
        log.info("created default calendar at %s", DEFAULT_CALENDAR_PATH)
        return DEFAULT_CALENDAR_PATH
    except Exception:
        log.exception("ensure_default_calendar: create_empty failed")
        return None


def sweep_missed(*, grace_minutes: int = 5) -> int:
    """Mark scheduled events past due as ``missed``.

    Called once on startup. An event is "missed" when its next-due occurrence
    is older than ``now - grace_minutes`` and the event is currently
    ``scheduled``. Recurring events: only flag missed if there is no upcoming
    occurrence in the future — otherwise they remain ``scheduled`` for the
    next firing.

    Returns the number of events marked missed.
    """
    now = datetime.now(UTC)
    cutoff = now - timedelta(minutes=grace_minutes)
    flagged = 0
    for summary in list_calendars():
        try:
            cal = read_calendar(summary.path)
        except (FileNotFoundError, OSError, ValueError):
            continue
        dirty = False
        for ev in cal.events:
            if ev.status != "scheduled":
                continue
            if effective_trigger(ev, cal) != "on_start":
                continue
            if ev.rrule:
                # If a future occurrence exists, leave it scheduled. Otherwise
                # all instances have passed → mark missed.
                future = next_occurrence_after(ev, now, tz=cal.timezone)
                if future is not None:
                    continue
                # All past — fall through to missed if last instance < cutoff.
                last = next_occurrence_after(ev, cutoff - timedelta(days=365 * 5), tz=cal.timezone)
                if last is None or last >= cutoff:
                    continue
            else:
                start = _parse_iso(ev.start)
                if start is None:
                    continue
                if start >= cutoff:
                    continue
            ev.status = "missed"
            dirty = True
            flagged += 1
        if dirty:
            try:
                write_calendar(summary.path, cal)
            except Exception:
                log.exception("sweep_missed: write_calendar failed for %s", summary.path)
    if flagged:
        log.info("sweep_missed: flagged %d events as missed", flagged)
    return flagged
