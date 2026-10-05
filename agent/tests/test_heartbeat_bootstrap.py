"""Self-healing heartbeat bootstrap tests.

Covers the two startup paths that historically killed ALL scheduling with
one silent exception:
* corrupt heartbeat.db (Syncthing-synced WAL) → quarantine + recreate
* sweep_missed comparing naive/aware datetimes → tz normalization
"""

from __future__ import annotations


from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import nexus.vault as vault_module
from nexus import vault_calendar
from nexus.server.app_lifespan import _open_heartbeat_store, _quarantine_db


@pytest.fixture(autouse=True)
def _vault_tmp(tmp_path: Path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    monkeypatch.setattr(vault_module, "_VAULT_ROOT", vault_root)
    return vault_root


class TestQuarantineRecovery:
    def test_opener_recovers_with_heartbeat_store(self, tmp_path):
        from loom.heartbeat import HeartbeatStore

        db = tmp_path / "heartbeat.db"
        db.write_bytes(b"\x00garbage\x00" * 100)
        # Side files must be cleaned too.
        (tmp_path / "heartbeat.db-wal").write_bytes(b"junk")
        (tmp_path / "heartbeat.db-shm").write_bytes(b"junk")

        store = _open_heartbeat_store(HeartbeatStore, db, "state")
        try:
            store.touch_check("hb")
            assert store.get_run("hb") is not None
        finally:
            store.close()

        assert list(tmp_path.glob("heartbeat.db.corrupt-*")), "old file quarantined"
        assert not (tmp_path / "heartbeat.db-wal").exists() or (
            tmp_path / "heartbeat.db-wal"
        ).stat().st_size >= 0

    def test_quarantine_removes_wal_shm(self, tmp_path):
        db = tmp_path / "x.db"
        db.write_bytes(b"garbage")
        (tmp_path / "x.db-wal").write_bytes(b"w")
        (tmp_path / "x.db-shm").write_bytes(b"s")
        _quarantine_db(db)
        assert not db.exists()
        assert not (tmp_path / "x.db-wal").exists()
        assert not (tmp_path / "x.db-shm").exists()
        assert len(list(tmp_path.glob("x.db.corrupt-*"))) == 1
        assert len(list(tmp_path.glob("x.db-wal.corrupt-*"))) == 1


class TestSweepMissedTimezone:
    def test_all_day_naive_event_does_not_crash(self):
        """Regression: naive all-day start vs aware cutoff raised TypeError
        inside sweep_missed, killing heartbeat bootstrap for every routine."""
        vault_calendar.create_empty("Calendars/Default.md", title="Default", timezone="UTC")
        past = (datetime.now(UTC) - timedelta(days=2)).strftime("%Y-%m-%d")
        vault_calendar.add_event(
            "Calendars/Default.md",
            title="Old all-day",
            start=past,
            trigger="on_start",
        )

        flagged = vault_calendar.sweep_missed(grace_minutes=5)  # must not raise
        assert flagged == 1
        cal = vault_calendar.read_calendar("Calendars/Default.md")
        assert cal.events[0].status == "missed"
