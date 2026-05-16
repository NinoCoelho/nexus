"""Alarm state persistence for calendar event user notifications.

Tracks per-occurrence alarm state so the heartbeat driver knows which
alarms have been emitted, acknowledged, or snoozed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class AlarmEntry:
    event_id: str
    occurrence_start: str  # UTC ISO of the occurrence this alarm is for
    status: str  # "ringing" | "acknowledged" | "snoozed"
    snoozed_until: str | None = None  # UTC ISO when snooze expires
    created_at: str | None = None
    calendar_path: str | None = None


class AlarmStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS alarm_state (
                event_id        TEXT NOT NULL,
                occurrence_start TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'ringing',
                snoozed_until   TEXT,
                created_at      TEXT,
                calendar_path   TEXT,
                PRIMARY KEY (event_id, occurrence_start)
            )
        """)
        self._db.commit()

    def get(self, event_id: str, occurrence_start: str) -> AlarmEntry | None:
        row = self._db.execute(
            "SELECT event_id, occurrence_start, status, snoozed_until, created_at, calendar_path "
            "FROM alarm_state WHERE event_id=? AND occurrence_start=?",
            (event_id, occurrence_start),
        ).fetchone()
        if not row:
            return None
        return AlarmEntry(
            event_id=row[0],
            occurrence_start=row[1],
            status=row[2],
            snoozed_until=row[3],
            created_at=row[4],
            calendar_path=row[5],
        )

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
        return [
            AlarmEntry(
                event_id=r[0],
                occurrence_start=r[1],
                status=r[2],
                snoozed_until=r[3],
                created_at=r[4],
                calendar_path=r[5],
            )
            for r in rows
        ]

    def list_snoozed_ready(self, now_utc: str) -> list[AlarmEntry]:
        rows = self._db.execute(
            "SELECT event_id, occurrence_start, status, snoozed_until, created_at, calendar_path "
            "FROM alarm_state WHERE status='snoozed' AND snoozed_until <= ?",
            (now_utc,),
        ).fetchall()
        return [
            AlarmEntry(
                event_id=r[0],
                occurrence_start=r[1],
                status=r[2],
                snoozed_until=r[3],
                created_at=r[4],
                calendar_path=r[5],
            )
            for r in rows
        ]

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

    def close(self) -> None:
        try:
            self._db.close()
        except Exception:
            pass
