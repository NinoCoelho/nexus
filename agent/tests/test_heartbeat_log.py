"""Tests for the heartbeat log store and API routes."""

import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nexus.heartbeat_log import HeartbeatLogStore, FireLogEntry


@pytest.fixture
def log_store(tmp_path: Path):
    db = tmp_path / "test_heartbeat.db"
    store = HeartbeatLogStore(db)
    yield store
    store.close()


class TestHeartbeatLogStore:
    def test_log_fire_and_read(self, log_store: HeartbeatLogStore):
        log_id = log_store.log_fire(
            heartbeat_id="calendar_trigger",
            event_id="ev-1",
            event_title="Morning check",
            calendar_path="cal/work.md",
            session_id="sess-1",
        )
        assert log_id > 0

        entries = log_store.list_log(heartbeat_id="calendar_trigger")
        assert len(entries) == 1
        e = entries[0]
        assert e.event_id == "ev-1"
        assert e.event_title == "Morning check"
        assert e.calendar_path == "cal/work.md"
        assert e.session_id == "sess-1"
        assert e.status == "running"

    def test_update_status(self, log_store: HeartbeatLogStore):
        log_id = log_store.log_fire(
            heartbeat_id="calendar_trigger",
            event_id="ev-2",
            event_title="Test",
        )
        log_store.update_status(log_id, status="done", duration_ms=1234)

        entries = log_store.list_log()
        assert len(entries) == 1
        assert entries[0].status == "done"
        assert entries[0].duration_ms == 1234

    def test_update_session_id(self, log_store: HeartbeatLogStore):
        log_id = log_store.log_fire(
            heartbeat_id="calendar_trigger",
            event_id="ev-3",
        )
        log_store.update_session_id(log_id, "sess-new")
        entries = log_store.list_log()
        assert entries[0].session_id == "sess-new"

    def test_failed_status(self, log_store: HeartbeatLogStore):
        log_id = log_store.log_fire(
            heartbeat_id="calendar_trigger",
            event_id="ev-4",
        )
        log_store.update_status(log_id, status="failed", error="timeout")
        entries = log_store.list_log()
        assert entries[0].status == "failed"
        assert entries[0].error == "timeout"

    def test_list_log_pagination(self, log_store: HeartbeatLogStore):
        for i in range(5):
            log_store.log_fire(
                heartbeat_id="hb1",
                event_id=f"ev-{i}",
                event_title=f"Event {i}",
            )
        for i in range(3):
            log_store.log_fire(
                heartbeat_id="hb2",
                event_id=f"ev-b-{i}",
                event_title=f"Event B {i}",
            )

        hb1 = log_store.list_log(heartbeat_id="hb1")
        assert len(hb1) == 5

        hb2 = log_store.list_log(heartbeat_id="hb2")
        assert len(hb2) == 3

        all_entries = log_store.list_log()
        assert len(all_entries) == 8

        limited = log_store.list_log(limit=3)
        assert len(limited) == 3

        offset = log_store.list_log(limit=3, offset=5)
        assert len(offset) == 3

    def test_get_latest_for_event(self, log_store: HeartbeatLogStore):
        log_store.log_fire(heartbeat_id="hb", event_id="ev-x")
        log_store.log_fire(heartbeat_id="hb", event_id="ev-y")
        log_store.log_fire(heartbeat_id="hb", event_id="ev-x", event_title="second run")

        latest = log_store.get_latest_for_event("ev-x")
        assert latest is not None
        assert latest.event_title == "second run"

        assert log_store.get_latest_for_event("nonexistent") is None

    def test_empty_log(self, log_store: HeartbeatLogStore):
        assert log_store.list_log() == []
        assert log_store.list_log(heartbeat_id="none") == []

    def test_close_idempotent(self, tmp_path: Path):
        store = HeartbeatLogStore(tmp_path / "test.db")
        store.close()
        store.close()
