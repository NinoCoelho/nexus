"""Per-heartbeat fire audit log persisted alongside heartbeat_state in heartbeat.db."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_TS_FMT = "%Y-%m-%dT%H:%M:%S.%f"


def _to_ts(dt: datetime) -> str:
    return dt.strftime(_TS_FMT)


def _from_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.strptime(s, _TS_FMT).replace(tzinfo=UTC)


@dataclass
class FireLogEntry:
    id: int
    timestamp: datetime
    heartbeat_id: str
    event_type: str
    event_id: str
    event_title: str
    calendar_path: str
    session_id: str | None
    status: str
    error: str | None
    duration_ms: int | None


_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS heartbeat_fire_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT    NOT NULL,
    heartbeat_id  TEXT    NOT NULL,
    event_type    TEXT    NOT NULL DEFAULT 'calendar',
    event_id      TEXT    NOT NULL,
    event_title   TEXT    NOT NULL DEFAULT '',
    calendar_path TEXT    NOT NULL DEFAULT '',
    session_id    TEXT,
    status        TEXT    NOT NULL DEFAULT 'running',
    error         TEXT,
    duration_ms   INTEGER
)
"""

_CREATE_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_fire_log_heartbeat_id ON heartbeat_fire_log(heartbeat_id)"
)
_CREATE_INDEX_TS = (
    "CREATE INDEX IF NOT EXISTS idx_fire_log_timestamp ON heartbeat_fire_log(timestamp DESC)"
)


class HeartbeatLogStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._closed = False
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(_CREATE_TABLE)
        self._db.execute(_CREATE_INDEX)
        self._db.execute(_CREATE_INDEX_TS)
        self._db.commit()

    def log_fire(
        self,
        *,
        heartbeat_id: str,
        event_id: str,
        event_title: str = "",
        calendar_path: str = "",
        session_id: str | None = None,
        event_type: str = "calendar",
    ) -> int:
        now = _to_ts(datetime.now(UTC))
        cur = self._db.execute(
            "INSERT INTO heartbeat_fire_log "
            "(timestamp, heartbeat_id, event_type, event_id, event_title, "
            "calendar_path, session_id, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'running')",
            (now, heartbeat_id, event_type, event_id, event_title,
             calendar_path, session_id),
        )
        self._db.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def update_status(
        self,
        log_id: int,
        *,
        status: str,
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        self._db.execute(
            "UPDATE heartbeat_fire_log SET status=?, error=?, duration_ms=? "
            "WHERE id=?",
            (status, error, duration_ms, log_id),
        )
        self._db.commit()

    def update_session_id(self, log_id: int, session_id: str) -> None:
        self._db.execute(
            "UPDATE heartbeat_fire_log SET session_id=? WHERE id=?",
            (session_id, log_id),
        )
        self._db.commit()

    def list_log(
        self,
        *,
        heartbeat_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[FireLogEntry]:
        if heartbeat_id is not None:
            rows = self._db.execute(
                "SELECT id, timestamp, heartbeat_id, event_type, event_id, "
                "event_title, calendar_path, session_id, status, error, duration_ms "
                "FROM heartbeat_fire_log WHERE heartbeat_id=? "
                "ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                (heartbeat_id, limit, offset),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT id, timestamp, heartbeat_id, event_type, event_id, "
                "event_title, calendar_path, session_id, status, error, duration_ms "
                "FROM heartbeat_fire_log ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [_row_to_entry(r) for r in rows]

    def get_latest_for_event(self, event_id: str) -> FireLogEntry | None:
        row = self._db.execute(
            "SELECT id, timestamp, heartbeat_id, event_type, event_id, "
            "event_title, calendar_path, session_id, status, error, duration_ms "
            "FROM heartbeat_fire_log WHERE event_id=? "
            "ORDER BY timestamp DESC LIMIT 1",
            (event_id,),
        ).fetchone()
        return _row_to_entry(row) if row else None

    def close(self) -> None:
        if self._closed:
            return
        self._db.close()
        self._closed = True

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def _row_to_entry(r: Any) -> FireLogEntry:
    return FireLogEntry(
        id=r[0],
        timestamp=_from_ts(r[1]),  # type: ignore[arg-type]
        heartbeat_id=r[2],
        event_type=r[3],
        event_id=r[4],
        event_title=r[5],
        calendar_path=r[6],
        session_id=r[7],
        status=r[8],
        error=r[9],
        duration_ms=r[10],
    )
