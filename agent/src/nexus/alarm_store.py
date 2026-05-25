"""Alarm state persistence for calendar event user notifications.

Tracks per-occurrence alarm state so the heartbeat driver knows which
alarms have been emitted, acknowledged, or snoozed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .sqlite_base import SqliteStore


@dataclass
class AlarmEntry:
    event_id: str
    occurrence_start: str  # UTC ISO of the occurrence this alarm is for
    status: str  # "ringing" | "acknowledged" | "snoozed"
    snoozed_until: str | None = None  # UTC ISO when snooze expires
    created_at: str | None = None
    calendar_path: str | None = None


_ALARM_SCHEMA = """
CREATE TABLE IF NOT EXISTS alarm_state (
    event_id        TEXT NOT NULL,
    occurrence_start TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'ringing',
    snoozed_until   TEXT,
    created_at      TEXT,
    calendar_path   TEXT,
    PRIMARY KEY (event_id, occurrence_start)
);
"""


class AlarmStore(SqliteStore):
    _SCHEMA = _ALARM_SCHEMA

    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)

    @staticmethod
    def _row_to_entry(row: tuple) -> AlarmEntry:
        return AlarmEntry(
            event_id=row[0],
            occurrence_start=row[1],
            status=row[2],
            snoozed_until=row[3],
            created_at=row[4],
            calendar_path=row[5],
        )

    def get(self, event_id: str, occurrence_start: str) -> AlarmEntry | None:
        row = self._db.execute(
            "SELECT event_id, occurrence_start, status, snoozed_until, created_at, calendar_path "
            "FROM alarm_state WHERE event_id=? AND occurrence_start=?",
            (event_id, occurrence_start),
        ).fetchone()
        if not row:
            return None
        return self._row_to_entry(row)

    def upsert(self, entry: AlarmEntry) -> None:
        if entry.created_at is None:
            entry.created_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._db.execute(
            "INSERT INTO alarm_state (event_id, occurrence_start, status, snoozed_until, created_at, calendar_path) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(event_id, occurrence_start) DO UPDATE SET "
            "status=excluded.status, snoozed_until=excluded.snoozed_until, "
            "calendar_path=excluded.calendar_path",
            (
                entry.event_id,
                entry.occurrence_start,
                entry.status,
                entry.snoozed_until,
                entry.created_at,
                entry.calendar_path,
            ),
        )
        self._db.commit()

    def acknowledge(self, event_id: str, occurrence_start: str) -> None:
        self._db.execute(
            "UPDATE alarm_state SET status='acknowledged', snoozed_until=NULL "
            "WHERE event_id=? AND occurrence_start=?",
            (event_id, occurrence_start),
        )
        self._db.commit()

    def snooze(self, event_id: str, occurrence_start: str, until_utc: str) -> None:
        self._db.execute(
            "UPDATE alarm_state SET status='snoozed', snoozed_until=? "
            "WHERE event_id=? AND occurrence_start=?",
            (until_utc, event_id, occurrence_start),
        )
        self._db.commit()

    def list_ringing(self) -> list[AlarmEntry]:
        rows = self._db.execute(
            "SELECT event_id, occurrence_start, status, snoozed_until, created_at, calendar_path "
            "FROM alarm_state WHERE status='ringing'"
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def list_snoozed_ready(self, now_utc: str) -> list[AlarmEntry]:
        rows = self._db.execute(
            "SELECT event_id, occurrence_start, status, snoozed_until, created_at, calendar_path "
            "FROM alarm_state WHERE status='snoozed' AND snoozed_until <= ?",
            (now_utc,),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def delete(self, event_id: str, occurrence_start: str) -> None:
        self._db.execute(
            "DELETE FROM alarm_state WHERE event_id=? AND occurrence_start=?",
            (event_id, occurrence_start),
        )
        self._db.commit()

    def garbage_collect(self, before_utc: str) -> int:
        """Remove acknowledged alarms older than the given timestamp."""
        cur = self._db.execute(
            "DELETE FROM alarm_state WHERE status='acknowledged' AND created_at < ?",
            (before_utc,),
        )
        self._db.commit()
        return cur.rowcount
