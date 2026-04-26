"""RRULE expansion utilities (iCal RFC 5545 subset).

Wraps :mod:`dateutil.rrule`. We never materialise the entire series — callers
ask for the next occurrence after a timestamp, or for occurrences inside a
finite window. This keeps memory bounded for unbounded rules like
``FREQ=DAILY`` without ``UNTIL`` or ``COUNT``.

All datetime computations happen in the calendar's local timezone (passed in
as an IANA name or "UTC") because RRULE day-of-week / day-of-month semantics
need a wall-clock view to honour DST. Inputs and outputs at the boundary are
UTC ISO-8601 strings.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from .models import Event


def _parse_iso(value: str) -> datetime | None:
    """Parse an ISO-8601 timestamp ('Z' or offset). Returns None on failure."""
    if not value:
        return None
    try:
        # Python 3.11+ accepts trailing 'Z' but be defensive for older formats.
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _to_iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")


def _zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except Exception:
        return ZoneInfo("UTC")


def next_occurrence_after(event: "Event", after_utc: datetime, tz: str = "UTC") -> datetime | None:
    """Return the next occurrence start (UTC) > ``after_utc``.

    For non-recurring events this is the event's own start, if it's after
    ``after_utc``; otherwise None. For recurring events, expands the RRULE
    starting from the original start time and returns the first instance
    strictly later than ``after_utc`` (UTC).
    """
    start = _parse_iso(event.start)
    if start is None:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=ZoneInfo("UTC"))
    if not event.rrule:
        return start if start > after_utc else None

    try:
        from dateutil.rrule import rrulestr
    except ImportError:
        # Without dateutil, fall back to single-shot semantics.
        return start if start > after_utc else None

    # Anchor RRULE in calendar TZ for correct DST handling.
    local_start = start.astimezone(_zone(tz))
    after_local = after_utc.astimezone(_zone(tz))
    try:
        rule = rrulestr(event.rrule, dtstart=local_start)
    except (ValueError, TypeError):
        return None
    nxt = rule.after(after_local, inc=False)
    if nxt is None:
        return None
    return nxt.astimezone(ZoneInfo("UTC"))


def expand_window(
    event: "Event",
    window_start_utc: datetime,
    window_end_utc: datetime,
    tz: str = "UTC",
) -> list[datetime]:
    """Return every occurrence of ``event`` whose start is in [start, end] (UTC).

    For non-recurring events the result is at most one element. For recurring
    events, the RRULE is expanded inside the window only.
    """
    start = _parse_iso(event.start)
    if start is None:
        return []
    if start.tzinfo is None:
        start = start.replace(tzinfo=ZoneInfo("UTC"))
    if not event.rrule:
        if window_start_utc <= start <= window_end_utc:
            return [start]
        return []

    try:
        from dateutil.rrule import rrulestr
    except ImportError:
        return [start] if window_start_utc <= start <= window_end_utc else []

    zone = _zone(tz)
    local_start = start.astimezone(zone)
    win_start_local = window_start_utc.astimezone(zone)
    win_end_local = window_end_utc.astimezone(zone)
    try:
        rule = rrulestr(event.rrule, dtstart=local_start)
    except (ValueError, TypeError):
        return []
    out = []
    for occ in rule.between(win_start_local, win_end_local, inc=True):
        out.append(occ.astimezone(ZoneInfo("UTC")))
    return out


def add_duration(start_iso: str, end_iso: str | None, new_start_iso: str) -> str | None:
    """Given an event moved from ``start_iso`` to ``new_start_iso``, return
    a new end ISO string preserving the original duration. Returns None if
    the original had no end.
    """
    if not end_iso:
        return None
    s = _parse_iso(start_iso)
    e = _parse_iso(end_iso)
    ns = _parse_iso(new_start_iso)
    if not (s and e and ns):
        return None
    duration = e - s
    if duration < timedelta(0):
        return None
    return _to_iso_utc(ns + duration)
