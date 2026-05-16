"""Dream state persistence — run history, budget tracking, concurrency lock."""

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
class DreamRun:
    id: int
    started_at: datetime
    finished_at: datetime | None
    depth: str
    phases_run: str
    status: str
    session_id: str | None
    tokens_in: int
    tokens_out: int
    duration_ms: int | None
    memories_merged: int
    insights_generated: int
    skills_created: int
    error: str | None


_CREATE_RUNS = """\
CREATE TABLE IF NOT EXISTS dream_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at        TEXT    NOT NULL,
    finished_at       TEXT,
    depth             TEXT    NOT NULL DEFAULT 'light',
    phases_run        TEXT    NOT NULL DEFAULT '',
    status            TEXT    NOT NULL DEFAULT 'running',
    session_id        TEXT,
    tokens_in         INTEGER NOT NULL DEFAULT 0,
    tokens_out        INTEGER NOT NULL DEFAULT 0,
    duration_ms       INTEGER,
    memories_merged   INTEGER NOT NULL DEFAULT 0,
    insights_generated INTEGER NOT NULL DEFAULT 0,
    skills_created    INTEGER NOT NULL DEFAULT 0,
    error             TEXT
)
"""

_CREATE_RUNS_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_dream_runs_started ON dream_runs(started_at DESC)"
)

_CREATE_BUDGET = """\
CREATE TABLE IF NOT EXISTS dream_budget (
    date         TEXT PRIMARY KEY,
    tokens_used  INTEGER NOT NULL DEFAULT 0
)
"""

_CREATE_TERRITORY = """\
CREATE TABLE IF NOT EXISTS dream_explored_territory (
    content_hash TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL
)
"""


class DreamStateStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._closed = False
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(_CREATE_RUNS)
        self._db.execute(_CREATE_RUNS_IDX)
        self._db.execute(_CREATE_BUDGET)
        self._db.execute(_CREATE_TERRITORY)
        self._db.commit()

    def start_run(self, *, depth: str = "light", phases: str = "") -> int:
        now = _to_ts(datetime.now(UTC))
        cur = self._db.execute(
            "INSERT INTO dream_runs (started_at, depth, phases_run, status) "
            "VALUES (?, ?, ?, 'running')",
            (now, depth, phases),
        )
        self._db.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def finish_run(
        self,
        run_id: int,
        *,
        status: str = "done",
        session_id: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        duration_ms: int | None = None,
        memories_merged: int = 0,
        insights_generated: int = 0,
        skills_created: int = 0,
        error: str | None = None,
    ) -> None:
        now = _to_ts(datetime.now(UTC))
        self._db.execute(
            "UPDATE dream_runs SET finished_at=?, status=?, session_id=?, "
            "tokens_in=?, tokens_out=?, duration_ms=?, memories_merged=?, "
            "insights_generated=?, skills_created=?, error=? WHERE id=?",
            (now, status, session_id, tokens_in, tokens_out, duration_ms,
             memories_merged, insights_generated, skills_created, error, run_id),
        )
        self._db.commit()

    def is_running(self) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM dream_runs WHERE status='running' LIMIT 1"
        ).fetchone()
        return row is not None

    def last_run(self) -> DreamRun | None:
        row = self._db.execute(
            "SELECT id, started_at, finished_at, depth, phases_run, status, "
            "session_id, tokens_in, tokens_out, duration_ms, memories_merged, "
            "insights_generated, skills_created, error "
            "FROM dream_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return _row_to_run(row) if row else None

    def list_runs(self, *, limit: int = 50, offset: int = 0) -> list[DreamRun]:
        rows = self._db.execute(
            "SELECT id, started_at, finished_at, depth, phases_run, status, "
            "session_id, tokens_in, tokens_out, duration_ms, memories_merged, "
            "insights_generated, skills_created, error "
            "FROM dream_runs ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [_row_to_run(r) for r in rows]

    def budget_used_today(self) -> int:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        row = self._db.execute(
            "SELECT tokens_used FROM dream_budget WHERE date=?", (today,)
        ).fetchone()
        return int(row[0]) if row else 0

    def add_budget_spend(self, tokens: int) -> None:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        self._db.execute(
            "INSERT INTO dream_budget (date, tokens_used) VALUES (?, ?) "
            "ON CONFLICT(date) DO UPDATE SET tokens_used = tokens_used + ?",
            (today, tokens, tokens),
        )
        self._db.commit()

    def has_explored(self, content_hash: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM dream_explored_territory WHERE content_hash=?",
            (content_hash,),
        ).fetchone()
        return row is not None

    def mark_explored(self, content_hash: str) -> None:
        now = _to_ts(datetime.now(UTC))
        self._db.execute(
            "INSERT OR IGNORE INTO dream_explored_territory (content_hash, created_at) "
            "VALUES (?, ?)",
            (content_hash, now),
        )
        self._db.commit()

    def cleanup_territory(self, max_age_days: int = 90) -> int:
        cutoff = datetime.now(UTC)
        from datetime import timedelta
        cutoff -= timedelta(days=max_age_days)
        cur = self._db.execute(
            "DELETE FROM dream_explored_territory WHERE created_at < ?",
            (_to_ts(cutoff),),
        )
        self._db.commit()
        return cur.rowcount

    def close(self) -> None:
        if self._closed:
            return
        self._db.close()
        self._closed = True


def _row_to_run(r: Any) -> DreamRun:
    return DreamRun(
        id=r[0],
        started_at=_from_ts(r[1]),  # type: ignore[arg-type]
        finished_at=_from_ts(r[2]),
        depth=r[3],
        phases_run=r[4],
        status=r[5],
        session_id=r[6],
        tokens_in=r[7],
        tokens_out=r[8],
        duration_ms=r[9],
        memories_merged=r[10],
        insights_generated=r[11],
        skills_created=r[12],
        error=r[13],
    )
