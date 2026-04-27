"""Tests for the calendar_trigger heartbeat driver.

Focus: recurring events must continue to fire on subsequent occurrences
regardless of the previous run's terminal status (done/failed/missed),
while still deduping the same occurrence across consecutive ticks.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import nexus.vault as vault_module
from nexus import calendar_runtime, vault_calendar
from nexus.heartbeat_drivers.calendar_trigger.driver import Driver


@pytest.fixture(autouse=True)
def _vault_tmp(tmp_path: Path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    monkeypatch.setattr(vault_module, "_VAULT_ROOT", vault_root)
    return vault_root


class _FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, *, path: str, event_id: str, mode: str) -> dict:
        self.calls.append({"path": path, "event_id": event_id, "mode": mode})
        return {"session_id": f"sid-{len(self.calls)}"}


@pytest.fixture
def fake_dispatcher(monkeypatch):
    fake = _FakeDispatcher()
    monkeypatch.setattr(calendar_runtime, "_dispatcher", fake)
    monkeypatch.setattr(calendar_runtime, "_notifier", None)
    yield fake
    monkeypatch.setattr(calendar_runtime, "_dispatcher", None)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def _tick(driver: Driver, state: dict, now: datetime) -> dict:
    """Run one driver tick at a frozen ``now`` and return the new state."""
    import nexus.heartbeat_drivers.calendar_trigger.driver as drv

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return now if tz is None else now.astimezone(tz)

    # Patch only the datetime symbol the driver imported.
    orig = drv.datetime
    drv.datetime = _FrozenDateTime  # type: ignore[assignment]
    try:
        _, new_state = await driver.check(state)
    finally:
        drv.datetime = orig  # type: ignore[assignment]
    return new_state


async def test_daily_recurring_fires_each_day(fake_dispatcher):
    """A daily event with status=done from yesterday must fire today."""
    vault_calendar.create_empty(
        "cal.md", title="C", timezone="UTC", prompt="run it",
    )
    # Start at Day-1 12:00, recur daily.
    ev = vault_calendar.add_event(
        "cal.md",
        title="Daily",
        start="2026-04-27T12:00:00Z",
        rrule="FREQ=DAILY",
        trigger="on_start",
        assignee="agent",
    )

    driver = Driver()
    state: dict = {}

    # Day 1 tick exactly at 12:00 UTC → should fire.
    day1 = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    state = await _tick(driver, state, day1)
    assert len(fake_dispatcher.calls) == 1, "day 1 should fire"

    # Simulate the dispatch pipeline marking the event triggered → done.
    vault_calendar.update_event("cal.md", ev.id, {"status": "done"})

    # One minute later, same day → must NOT re-fire (same occurrence).
    state = await _tick(driver, state, day1 + timedelta(minutes=1))
    assert len(fake_dispatcher.calls) == 1, "same occurrence dedup"

    # Day 2 at 12:00 UTC → must fire again even though status is "done".
    day2 = day1 + timedelta(days=1)
    state = await _tick(driver, state, day2)
    assert len(fake_dispatcher.calls) == 2, "day 2 must fire despite status=done"


async def test_recurring_after_failed_still_fires_next_day(fake_dispatcher):
    """status=failed on a recurring event must not freeze it forever."""
    vault_calendar.create_empty("cal.md", prompt="x")
    ev = vault_calendar.add_event(
        "cal.md",
        title="Daily",
        start="2026-04-27T12:00:00Z",
        rrule="FREQ=DAILY",
        trigger="on_start",
        assignee="agent",
    )
    vault_calendar.update_event("cal.md", ev.id, {"status": "failed"})

    driver = Driver()
    state: dict = {}
    day2 = datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC)
    await _tick(driver, state, day2)
    assert len(fake_dispatcher.calls) == 1


async def test_recurring_after_missed_still_fires_next_day(fake_dispatcher):
    """status=missed on a recurring event must not freeze it forever."""
    vault_calendar.create_empty("cal.md", prompt="x")
    ev = vault_calendar.add_event(
        "cal.md",
        title="Daily",
        start="2026-04-27T12:00:00Z",
        rrule="FREQ=DAILY",
        trigger="on_start",
        assignee="agent",
    )
    vault_calendar.update_event("cal.md", ev.id, {"status": "missed"})

    driver = Driver()
    state: dict = {}
    day2 = datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC)
    await _tick(driver, state, day2)
    assert len(fake_dispatcher.calls) == 1


async def test_cancelled_recurring_never_fires(fake_dispatcher):
    """User-cancelled events must not auto-fire even if recurring."""
    vault_calendar.create_empty("cal.md", prompt="x")
    ev = vault_calendar.add_event(
        "cal.md",
        title="Daily",
        start="2026-04-27T12:00:00Z",
        rrule="FREQ=DAILY",
        trigger="on_start",
        assignee="agent",
    )
    vault_calendar.update_event("cal.md", ev.id, {"status": "cancelled"})

    driver = Driver()
    state: dict = {}
    day2 = datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC)
    await _tick(driver, state, day2)
    assert fake_dispatcher.calls == []


async def test_recurring_past_due_not_marked_missed(fake_dispatcher):
    """A recurring event with a stale past occurrence must not be flagged
    missed by the driver; the next future occurrence still fires."""
    vault_calendar.create_empty("cal.md", prompt="x")
    vault_calendar.add_event(
        "cal.md",
        title="Daily",
        start="2020-01-01T12:00:00Z",  # ancient
        rrule="FREQ=DAILY",
        trigger="on_start",
        assignee="agent",
    )

    driver = Driver()
    # Cold start: last_processed defaults to now-_GRACE. A past-due
    # occurrence between (last_processed - GRACE) and now is fine; way
    # past-due (e.g., yesterday) would historically mark single-shot
    # events missed. For recurring, must be skipped silently.
    state: dict = {}
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)  # 6+ years later
    await _tick(driver, state, now)

    cal = vault_calendar.read_calendar("cal.md")
    assert cal.events[0].status == "scheduled", "must not be flagged missed"


async def test_unassigned_event_never_fires(fake_dispatcher):
    """Events without assignee="agent" are plain entries — never fire."""
    vault_calendar.create_empty("cal.md", prompt="x")
    vault_calendar.add_event(
        "cal.md",
        title="Daily plain",
        start="2026-04-27T12:00:00Z",
        rrule="FREQ=DAILY",
        trigger="on_start",
        # no assignee
    )

    driver = Driver()
    state: dict = {}
    day1 = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    await _tick(driver, state, day1)
    assert fake_dispatcher.calls == []
