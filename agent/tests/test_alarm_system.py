"""Tests for alarm store and calendar alarm driver path."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import nexus.vault as vault_module
from nexus import calendar_runtime, vault_calendar
from nexus.alarm_store import AlarmEntry, AlarmStore
from nexus.heartbeat_drivers.calendar_trigger.driver import Driver


@pytest.fixture(autouse=True)
def _vault_tmp(tmp_path: Path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    monkeypatch.setattr(vault_module, "_VAULT_ROOT", vault_root)
    return vault_root


@pytest.fixture
def alarm_db(tmp_path: Path):
    db = AlarmStore(tmp_path / "test_alarms.db")
    yield db
    db.close()


class _FakeDispatcher:
    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, **kw):
        self.calls.append(kw)
        return {"session_id": f"sid-{len(self.calls)}"}


class _FakeNotifier:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, payload: dict):
        self.calls.append(payload)


@pytest.fixture
def fake_dispatcher(monkeypatch):
    fake = _FakeDispatcher()
    monkeypatch.setattr(calendar_runtime, "_dispatcher", fake)
    monkeypatch.setattr(calendar_runtime, "_notifier", None)
    monkeypatch.setattr(calendar_runtime, "_alarm_store", None)
    yield fake
    monkeypatch.setattr(calendar_runtime, "_dispatcher", None)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def _tick(driver: Driver, state: dict, now: datetime) -> dict:
    import nexus.heartbeat_drivers.calendar_trigger.driver as drv

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is None else now.astimezone(tz)

    orig = drv.datetime
    drv.datetime = _FrozenDateTime
    try:
        _, new_state = await driver.check(state)
    finally:
        drv.datetime = orig
    return new_state


# ── Alarm Store Tests ────────────────────────────────────────────────────────


def test_alarm_store_upsert_and_get(alarm_db: AlarmStore):
    alarm_db.upsert(AlarmEntry(
        event_id="ev1",
        occurrence_start="2026-05-01T12:00:00Z",
        status="ringing",
        calendar_path="cal.md",
    ))
    entry = alarm_db.get("ev1", "2026-05-01T12:00:00Z")
    assert entry is not None
    assert entry.status == "ringing"
    assert entry.calendar_path == "cal.md"


def test_alarm_store_acknowledge(alarm_db: AlarmStore):
    alarm_db.upsert(AlarmEntry(
        event_id="ev1",
        occurrence_start="2026-05-01T12:00:00Z",
        status="ringing",
    ))
    alarm_db.acknowledge("ev1", "2026-05-01T12:00:00Z")
    entry = alarm_db.get("ev1", "2026-05-01T12:00:00Z")
    assert entry is not None
    assert entry.status == "acknowledged"


def test_alarm_store_snooze(alarm_db: AlarmStore):
    alarm_db.upsert(AlarmEntry(
        event_id="ev1",
        occurrence_start="2026-05-01T12:00:00Z",
        status="ringing",
    ))
    alarm_db.snooze("ev1", "2026-05-01T12:00:00Z", "2026-05-01T12:10:00Z")
    entry = alarm_db.get("ev1", "2026-05-01T12:00:00Z")
    assert entry is not None
    assert entry.status == "snoozed"
    assert entry.snoozed_until == "2026-05-01T12:10:00Z"


def test_alarm_store_list_ringing(alarm_db: AlarmStore):
    alarm_db.upsert(AlarmEntry(event_id="ev1", occurrence_start="2026-05-01T12:00:00Z", status="ringing"))
    alarm_db.upsert(AlarmEntry(event_id="ev2", occurrence_start="2026-05-01T13:00:00Z", status="acknowledged"))
    alarm_db.upsert(AlarmEntry(event_id="ev3", occurrence_start="2026-05-01T14:00:00Z", status="ringing"))
    ringing = alarm_db.list_ringing()
    assert len(ringing) == 2
    ids = {r.event_id for r in ringing}
    assert ids == {"ev1", "ev3"}


def test_alarm_store_list_snoozed_ready(alarm_db: AlarmStore):
    alarm_db.upsert(AlarmEntry(event_id="ev1", occurrence_start="o1", status="snoozed", snoozed_until="2026-05-01T12:05:00Z"))
    alarm_db.upsert(AlarmEntry(event_id="ev2", occurrence_start="o2", status="snoozed", snoozed_until="2026-05-01T13:00:00Z"))
    ready = alarm_db.list_snoozed_ready("2026-05-01T12:10:00Z")
    assert len(ready) == 1
    assert ready[0].event_id == "ev1"


def test_alarm_store_garbage_collect(alarm_db: AlarmStore):
    alarm_db.upsert(AlarmEntry(event_id="ev1", occurrence_start="o1", status="acknowledged"))
    alarm_db.upsert(AlarmEntry(event_id="ev2", occurrence_start="o2", status="ringing"))
    count = alarm_db.garbage_collect("2099-01-01T00:00:00Z")
    assert count == 1
    assert alarm_db.get("ev1", "o1") is None
    assert alarm_db.get("ev2", "o2") is not None


# ── Dual Assignee Model Tests ────────────────────────────────────────────────


def test_dual_assignee_agent_and_user():
    from nexus.vault_calendar.models import Event
    ev = Event(id="x", title="t", start="s", assignee="agent,user")
    assert ev.is_agent_assigned
    assert ev.has_user_alarm


def test_assignee_agent_only():
    from nexus.vault_calendar.models import Event
    ev = Event(id="x", title="t", start="s", assignee="agent")
    assert ev.is_agent_assigned
    assert not ev.has_user_alarm


def test_assignee_user_only():
    from nexus.vault_calendar.models import Event
    ev = Event(id="x", title="t", start="s", assignee="user")
    assert not ev.is_agent_assigned
    assert ev.has_user_alarm


def test_assignee_none():
    from nexus.vault_calendar.models import Event
    ev = Event(id="x", title="t", start="s", assignee=None)
    assert not ev.is_agent_assigned
    assert not ev.has_user_alarm


def test_remind_before_min_parse_serialize(tmp_path: Path):
    vault_calendar.create_empty("cal.md", title="C", timezone="UTC")
    ev = vault_calendar.add_event(
        "cal.md",
        title="Meeting",
        start="2026-05-01T12:00:00Z",
        assignee="user",
        remind_before_min=15,
    )
    cal = vault_calendar.read_calendar("cal.md")
    found = vault_calendar.find_event(cal, ev.id)
    assert found is not None
    assert found[0].remind_before_min == 15
    assert found[0].assignee == "user"
    assert found[0].has_user_alarm
    assert found[0].alarm_lead_seconds == 900


# ── Driver Alarm Path Tests ──────────────────────────────────────────────────


async def test_user_alarm_fires_at_start_time(fake_dispatcher, tmp_path: Path):
    vault_calendar.create_empty("cal.md", prompt="x")
    vault_calendar.add_event(
        "cal.md",
        title="Meeting",
        start="2026-04-27T12:00:00Z",
        assignee="user",
        remind_before_min=0,
    )

    notifier = _FakeNotifier()
    monkeypatch_notif = pytest.MonkeyPatch()
    monkeypatch_notif.setattr(calendar_runtime, "_notifier", notifier)

    alarm_db = AlarmStore(tmp_path / "alarm.db")
    monkeypatch_notif.setattr(calendar_runtime, "_alarm_store", alarm_db)

    try:
        driver = Driver()
        state: dict = {}
        now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
        state = await _tick(driver, state, now)

        assert len(notifier.calls) == 1
        assert notifier.calls[0]["type"] == "calendar_alarm"
        assert notifier.calls[0]["title"] == "Meeting"
        assert notifier.calls[0]["countdown_seconds"] == 0
        assert notifier.calls[0]["is_overdue"] is False
    finally:
        monkeypatch_notif.undo()
        alarm_db.close()


async def test_user_alarm_with_lead_time(fake_dispatcher, tmp_path: Path):
    vault_calendar.create_empty("cal.md", prompt="x")
    vault_calendar.add_event(
        "cal.md",
        title="Meeting",
        start="2026-04-27T12:00:00Z",
        assignee="user",
        remind_before_min=15,
    )

    notifier = _FakeNotifier()
    monkeypatch_notif = pytest.MonkeyPatch()
    monkeypatch_notif.setattr(calendar_runtime, "_notifier", notifier)

    alarm_db = AlarmStore(tmp_path / "alarm.db")
    monkeypatch_notif.setattr(calendar_runtime, "_alarm_store", alarm_db)

    try:
        driver = Driver()
        state: dict = {}

        now_15_before = datetime(2026, 4, 27, 11, 45, 0, tzinfo=UTC)
        state = await _tick(driver, state, now_15_before)
        assert len(notifier.calls) == 1
        assert notifier.calls[0]["countdown_seconds"] == 900
        assert notifier.calls[0]["is_overdue"] is False

        notifier.calls.clear()
        state = await _tick(driver, state, now_15_before + timedelta(minutes=1))
        assert len(notifier.calls) == 0, "dedup: same occurrence should not re-fire"
    finally:
        monkeypatch_notif.undo()
        alarm_db.close()


async def test_dual_assignee_fires_both(fake_dispatcher, tmp_path: Path):
    vault_calendar.create_empty("cal.md", prompt="x")
    vault_calendar.add_event(
        "cal.md",
        title="Standup",
        start="2026-04-27T12:00:00Z",
        assignee="agent,user",
        trigger="on_start",
        remind_before_min=0,
    )

    notifier = _FakeNotifier()
    monkeypatch_notif = pytest.MonkeyPatch()
    monkeypatch_notif.setattr(calendar_runtime, "_notifier", notifier)

    alarm_db = AlarmStore(tmp_path / "alarm.db")
    monkeypatch_notif.setattr(calendar_runtime, "_alarm_store", alarm_db)

    try:
        driver = Driver()
        state: dict = {}
        now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
        state = await _tick(driver, state, now)

        assert len(fake_dispatcher.calls) == 1, "agent should dispatch"
        assert len(notifier.calls) == 1, "user should get alarm"
    finally:
        monkeypatch_notif.undo()
        alarm_db.close()


async def test_alarm_does_not_fire_before_time(fake_dispatcher, tmp_path: Path):
    vault_calendar.create_empty("cal.md", prompt="x")
    vault_calendar.add_event(
        "cal.md",
        title="Meeting",
        start="2026-04-27T12:00:00Z",
        assignee="user",
        remind_before_min=0,
    )

    notifier = _FakeNotifier()
    monkeypatch_notif = pytest.MonkeyPatch()
    monkeypatch_notif.setattr(calendar_runtime, "_notifier", notifier)
    monkeypatch_notif.setattr(calendar_runtime, "_alarm_store", None)

    try:
        driver = Driver()
        state: dict = {}
        now = datetime(2026, 4, 27, 11, 59, 0, tzinfo=UTC)
        state = await _tick(driver, state, now)
        assert len(notifier.calls) == 0, "should not fire before start time"
    finally:
        monkeypatch_notif.undo()


async def test_acknowledged_alarm_not_re_fired(fake_dispatcher, tmp_path: Path):
    vault_calendar.create_empty("cal.md", prompt="x")
    vault_calendar.add_event(
        "cal.md",
        title="Meeting",
        start="2026-04-27T12:00:00Z",
        assignee="user",
        remind_before_min=0,
    )

    notifier = _FakeNotifier()
    monkeypatch_notif = pytest.MonkeyPatch()
    monkeypatch_notif.setattr(calendar_runtime, "_notifier", notifier)

    alarm_db = AlarmStore(tmp_path / "alarm.db")
    monkeypatch_notif.setattr(calendar_runtime, "_alarm_store", alarm_db)

    try:
        driver = Driver()
        state: dict = {}

        now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
        state = await _tick(driver, state, now)
        assert len(notifier.calls) == 1

        alarm_db.acknowledge(list(alarm_db.list_ringing())[0].event_id, "2026-04-27T12:00:00Z")

        notifier.calls.clear()
        state = await _tick(driver, state, now + timedelta(minutes=1))
        assert len(notifier.calls) == 0, "acknowledged alarm should not re-fire"
    finally:
        monkeypatch_notif.undo()
        alarm_db.close()


async def test_no_remind_before_min_fires_at_start_time(fake_dispatcher, tmp_path: Path):
    vault_calendar.create_empty("cal.md", prompt="x")
    vault_calendar.add_event(
        "cal.md",
        title="Meeting",
        start="2026-04-27T12:00:00Z",
        assignee="user",
    )

    notifier = _FakeNotifier()
    monkeypatch_notif = pytest.MonkeyPatch()
    monkeypatch_notif.setattr(calendar_runtime, "_notifier", notifier)
    monkeypatch_notif.setattr(calendar_runtime, "_alarm_store", None)

    try:
        driver = Driver()
        state: dict = {}
        now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
        state = await _tick(driver, state, now)
        assert len(notifier.calls) == 1, "user alarm fires at start time when remind_before_min is None"
    finally:
        monkeypatch_notif.undo()
