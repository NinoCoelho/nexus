"""Tests for the vault_calendar parser, CRUD, recurrence, and tool."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import nexus.vault as vault_module
from nexus import vault_calendar
from nexus.tools.calendar_tool import handle_calendar_tool


@pytest.fixture(autouse=True)
def _vault_tmp(tmp_path: Path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    monkeypatch.setattr(vault_module, "_VAULT_ROOT", vault_root)
    return vault_root


def test_parse_serialize_round_trip():
    vault_calendar.create_empty("a.md", title="Personal", timezone="UTC", prompt="Be helpful")
    ev = vault_calendar.add_event(
        "a.md",
        title="Standup",
        start="2026-04-27T13:00:00Z",
        end="2026-04-27T13:30:00Z",
        body="daily sync",
        trigger="on_start",
    )
    cal = vault_calendar.read_calendar("a.md")
    assert cal.title == "Personal"
    assert cal.timezone == "UTC"
    assert cal.calendar_prompt == "Be helpful"
    assert cal.auto_trigger is True
    found = cal.events[0]
    assert found.id == ev.id
    assert found.start == "2026-04-27T13:00:00Z"
    assert found.end == "2026-04-27T13:30:00Z"
    assert found.trigger == "on_start"
    assert found.status == "scheduled"
    assert found.body == "daily sync"


def test_event_status_transitions():
    vault_calendar.create_empty("a.md")
    ev = vault_calendar.add_event("a.md", title="X", start="2026-04-27T13:00:00Z")
    vault_calendar.update_event("a.md", ev.id, {"status": "triggered", "session_id": "sid-123"})
    cal = vault_calendar.read_calendar("a.md")
    assert cal.events[0].status == "triggered"
    assert cal.events[0].session_id == "sid-123"

    with pytest.raises(ValueError):
        vault_calendar.update_event("a.md", ev.id, {"status": "bogus"})


def test_move_event_preserves_duration():
    vault_calendar.create_empty("a.md")
    ev = vault_calendar.add_event(
        "a.md", title="X",
        start="2026-04-27T13:00:00Z",
        end="2026-04-27T14:00:00Z",
    )
    moved = vault_calendar.move_event("a.md", ev.id, "2026-04-27T15:30:00Z")
    assert moved.start == "2026-04-27T15:30:00Z"
    assert moved.end == "2026-04-27T16:30:00Z"


def test_recurrence_next_occurrence():
    from nexus.vault_calendar.models import Event
    ev = Event(id="r", title="X", start="2026-04-27T13:00:00Z",
               rrule="FREQ=WEEKLY;BYDAY=MO,WE,FR")
    after = datetime(2026, 4, 27, 13, 0, 0, tzinfo=UTC)
    nxt = vault_calendar.next_occurrence_after(ev, after, tz="UTC")
    # Mon → Wed
    assert nxt is not None
    assert nxt.weekday() == 2  # Wednesday


def test_list_calendars_and_default_trigger():
    vault_calendar.create_empty("a.md", title="A")
    vault_calendar.create_empty("b.md", title="B")
    summaries = vault_calendar.list_calendars()
    paths = [s.path for s in summaries]
    assert "a.md" in paths and "b.md" in paths


def test_effective_trigger():
    from nexus.vault_calendar.models import Calendar, Event
    cal = Calendar(title="X", frontmatter={"calendar-plugin": "basic", "auto_trigger": True})
    cal_off = Calendar(title="Y", frontmatter={"calendar-plugin": "basic", "auto_trigger": False})
    ev = Event(id="e", title="x", start="2026-04-27T13:00:00Z")
    ev_off = Event(id="e2", title="y", start="2026-04-27T13:00:00Z", trigger="off")
    ev_on = Event(id="e3", title="z", start="2026-04-27T13:00:00Z", trigger="on_start")
    assert vault_calendar.effective_trigger(ev, cal) == "on_start"
    assert vault_calendar.effective_trigger(ev, cal_off) == "off"
    assert vault_calendar.effective_trigger(ev_off, cal) == "off"
    assert vault_calendar.effective_trigger(ev_on, cal_off) == "on_start"


def test_calendar_tool_actions():
    res = json.loads(handle_calendar_tool({
        "action": "create_calendar",
        "path": "x.md",
        "title": "X",
        "timezone": "America/Sao_Paulo",
    }))
    assert res["ok"] is True

    res = json.loads(handle_calendar_tool({
        "action": "add_event",
        "path": "x.md",
        "title": "Standup",
        "start": "2026-04-27T13:00:00Z",
        "end": "2026-04-27T13:30:00Z",
        "trigger": "on_start",
    }))
    assert res["ok"] is True
    event_id = res["event"]["id"]

    res = json.loads(handle_calendar_tool({
        "action": "update_event",
        "path": "x.md",
        "event_id": event_id,
        "status": "done",
    }))
    assert res["ok"] is True
    assert res["event"]["status"] == "done"

    res = json.loads(handle_calendar_tool({
        "action": "list_events",
        "path": "x.md",
    }))
    assert res["ok"] is True
    assert res["count"] == 1


def test_default_bootstrap_idempotent():
    # No calendars yet -> creates Default
    created = vault_calendar.ensure_default_calendar()
    assert created == "Calendars/Default.md"
    # Already exists -> no-op
    assert vault_calendar.ensure_default_calendar() is None
    cal = vault_calendar.read_calendar("Calendars/Default.md")
    assert cal.title == "Default"


def test_legacy_event_prompt_normalises_to_assignee_agent():
    """Legacy events written with `<!-- nx:prompt=... -->` (and no explicit
    assignee) opted into agent dispatch implicitly. After the field was
    removed, the parser must still infer ``assignee=agent`` so they keep
    firing; the legacy prompt value itself is discarded."""
    legacy = (
        "---\ncalendar-plugin: basic\n---\n\n"
        "# Calendar\n\n"
        "### Legacy\n"
        "<!-- nx:id=legacy-1 -->\n"
        "<!-- nx:start=2026-04-27T13:00:00Z -->\n"
        "<!-- nx:status=scheduled -->\n"
        "<!-- nx:prompt=do%20it -->\n"
    )
    import nexus.vault as vault_module
    (vault_module._VAULT_ROOT / "a.md").write_text(legacy)
    cal = vault_calendar.read_calendar("a.md")
    assert cal.events[0].assignee == "agent"
    # The field no longer exists on the dataclass.
    assert not hasattr(cal.events[0], "prompt")


def test_fire_window_round_trip():
    vault_calendar.create_empty("a.md")
    ev = vault_calendar.add_event(
        "a.md",
        title="News check",
        start="2026-04-27",
        all_day=True,
        rrule="FREQ=DAILY",
        trigger="on_start",
        fire_from="09:00",
        fire_to="18:00",
        fire_every_min=30,
    )
    assert ev.has_fire_window
    cal = vault_calendar.read_calendar("a.md")
    found = cal.events[0]
    assert found.fire_from == "09:00"
    assert found.fire_to == "18:00"
    assert found.fire_every_min == 30
    assert found.has_fire_window is True


def test_fire_window_validation():
    vault_calendar.create_empty("a.md")
    with pytest.raises(ValueError):
        vault_calendar.add_event(
            "a.md", title="X", start="2026-04-27", all_day=True,
            fire_from="bogus", fire_to="18:00", fire_every_min=30,
        )
    with pytest.raises(ValueError):
        vault_calendar.add_event(
            "a.md", title="X", start="2026-04-27", all_day=True,
            fire_from="09:00", fire_to="18:00", fire_every_min=99999,
        )


def test_fire_window_via_tool():
    res = json.loads(handle_calendar_tool({
        "action": "create_calendar", "path": "x.md", "title": "X",
    }))
    assert res["ok"]
    res = json.loads(handle_calendar_tool({
        "action": "add_event",
        "path": "x.md",
        "title": "Hourly check",
        "start": "2026-04-27",
        "all_day": True,
        "rrule": "FREQ=DAILY",
        "trigger": "on_start",
        "fire_from": "09:00",
        "fire_to": "17:00",
        "fire_every_min": 60,
    }))
    assert res["ok"], res
    assert res["event"]["fire_from"] == "09:00"
    assert res["event"]["fire_every_min"] == 60


def test_sweep_missed_flags_old_scheduled_events():
    vault_calendar.create_empty("a.md")
    vault_calendar.add_event(
        "a.md", title="Past", start="2020-01-01T00:00:00Z", trigger="on_start",
    )
    vault_calendar.add_event(
        "a.md", title="Future", start="2099-01-01T00:00:00Z", trigger="on_start",
    )
    flagged = vault_calendar.sweep_missed(grace_minutes=5)
    assert flagged == 1
    cal = vault_calendar.read_calendar("a.md")
    statuses = {ev.title: ev.status for ev in cal.events}
    assert statuses["Past"] == "missed"
    assert statuses["Future"] == "scheduled"
