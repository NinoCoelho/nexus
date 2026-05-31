"""SQLite store for workflow run history and state."""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

from .models import RunStatus, StepRun, StepRunStatus, TriggerType, WorkflowRun

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    id TEXT PRIMARY KEY,
    workflow_path TEXT NOT NULL,
    trigger_id TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    trigger_payload TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    current_step TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS step_runs (
    run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    input_resolved TEXT,
    output TEXT,
    error TEXT,
    started_at TEXT,
    finished_at TEXT,
    PRIMARY KEY (run_id, step_id)
);

CREATE TABLE IF NOT EXISTS webhook_tokens (
    token TEXT PRIMARY KEY,
    workflow_path TEXT NOT NULL,
    trigger_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fs_watch_seen (
    trigger_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    event_type TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    PRIMARY KEY (trigger_id, file_path, event_type)
);

CREATE INDEX IF NOT EXISTS idx_runs_workflow ON workflow_runs(workflow_path);
CREATE INDEX IF NOT EXISTS idx_runs_status ON workflow_runs(status);

CREATE TABLE IF NOT EXISTS rss_seen (
    trigger_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    PRIMARY KEY (trigger_id, item_id)
);
"""

_lock = threading.Lock()


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


class WorkflowStore:
    def __init__(self, db_path: str) -> None:
        self._conn = _connect(db_path)
        with self._conn:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def create_run(self, run: WorkflowRun) -> None:
        with _lock:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO workflow_runs (id, workflow_path, trigger_id, trigger_type, "
                    "trigger_payload, status, started_at, finished_at, current_step, error) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run.id,
                        run.workflow_path,
                        run.trigger_id,
                        run.trigger_type.value,
                        json.dumps(run.trigger_payload, default=str),
                        run.status.value,
                        run.started_at,
                        run.finished_at,
                        run.current_step,
                        run.error,
                    ),
                )

    def update_run(self, run: WorkflowRun) -> None:
        with _lock:
            with self._conn:
                self._conn.execute(
                    "UPDATE workflow_runs SET status=?, finished_at=?, current_step=?, error=? "
                    "WHERE id=?",
                    (run.status.value, run.finished_at, run.current_step, run.error, run.id),
                )

    def get_run(self, run_id: str) -> WorkflowRun | None:
        with _lock:
            row = self._conn.execute(
                "SELECT * FROM workflow_runs WHERE id=?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_run(row)

    def list_runs(self, workflow_path: str, limit: int = 50, offset: int = 0) -> list[WorkflowRun]:
        with _lock:
            rows = self._conn.execute(
                "SELECT * FROM workflow_runs WHERE workflow_path=? "
                "ORDER BY started_at DESC LIMIT ? OFFSET ?",
                (workflow_path, limit, offset),
            ).fetchall()
        return [self._row_to_run(r) for r in rows]

    def create_step_run(self, step_run: StepRun) -> None:
        with _lock:
            with self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO step_runs (run_id, step_id, status, input_resolved, output, "
                    "error, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        step_run.run_id,
                        step_run.step_id,
                        step_run.status.value,
                        json.dumps(step_run.input_resolved, default=str) if step_run.input_resolved else None,
                        json.dumps(step_run.output, default=str) if step_run.output is not None else None,
                        step_run.error,
                        step_run.started_at,
                        step_run.finished_at,
                    ),
                )

    def update_step_run(self, step_run: StepRun) -> None:
        with _lock:
            with self._conn:
                self._conn.execute(
                    "UPDATE step_runs SET status=?, output=?, error=?, finished_at=? "
                    "WHERE run_id=? AND step_id=?",
                    (
                        step_run.status.value,
                        json.dumps(step_run.output, default=str) if step_run.output is not None else None,
                        step_run.error,
                        step_run.finished_at,
                        step_run.run_id,
                        step_run.step_id,
                    ),
                )

    def list_step_runs(self, run_id: str) -> list[StepRun]:
        with _lock:
            rows = self._conn.execute(
                "SELECT * FROM step_runs WHERE run_id=? ORDER BY rowid",
                (run_id,),
            ).fetchall()
        return [self._row_to_step_run(r) for r in rows]

    def register_webhook_token(self, token: str, workflow_path: str, trigger_id: str, created_at: str) -> None:
        with _lock:
            with self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO webhook_tokens (token, workflow_path, trigger_id, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (token, workflow_path, trigger_id, created_at),
                )

    def lookup_webhook_token(self, token: str) -> tuple[str, str] | None:
        with _lock:
            row = self._conn.execute(
                "SELECT workflow_path, trigger_id FROM webhook_tokens WHERE token=?",
                (token,),
            ).fetchone()
        if row is None:
            return None
        return row["workflow_path"], row["trigger_id"]

    def remove_webhook_tokens(self, workflow_path: str) -> None:
        with _lock:
            with self._conn:
                self._conn.execute(
                    "DELETE FROM webhook_tokens WHERE workflow_path=?",
                    (workflow_path,),
                )

    def reconcile_stale_runs(self) -> int:
        import datetime
        now = datetime.datetime.utcnow().isoformat()
        with _lock:
            with self._conn:
                cur = self._conn.execute(
                    "UPDATE workflow_runs SET status='failed', error='server restarted', finished_at=? "
                    "WHERE status='running'",
                    (now,),
                )
                updated = cur.rowcount
                cur2 = self._conn.execute(
                    "DELETE FROM workflow_runs WHERE status='pending'"
                )
                orphaned = cur2.rowcount
                self._conn.execute(
                    "DELETE FROM step_runs WHERE run_id NOT IN (SELECT id FROM workflow_runs)"
                )
        return updated + orphaned

    def is_fs_seen(self, trigger_id: str, file_path: str, event_type: str) -> bool:
        with _lock:
            row = self._conn.execute(
                "SELECT 1 FROM fs_watch_seen WHERE trigger_id=? AND file_path=? AND event_type=?",
                (trigger_id, file_path, event_type),
            ).fetchone()
        return row is not None

    def mark_fs_seen(self, trigger_id: str, file_path: str, event_type: str) -> None:
        import datetime
        with _lock:
            with self._conn:
                self._conn.execute(
                    "INSERT OR IGNORE INTO fs_watch_seen (trigger_id, file_path, event_type, seen_at) "
                    "VALUES (?, ?, ?, ?)",
                    (trigger_id, file_path, event_type, datetime.datetime.utcnow().isoformat()),
                )

    def clear_fs_seen(self, trigger_id: str) -> int:
        with _lock:
            with self._conn:
                cur = self._conn.execute(
                    "DELETE FROM fs_watch_seen WHERE trigger_id=?", (trigger_id,)
                )
        return cur.rowcount

    def is_rss_seen(self, trigger_id: str, item_id: str) -> bool:
        with _lock:
            row = self._conn.execute(
                "SELECT 1 FROM rss_seen WHERE trigger_id=? AND item_id=?",
                (trigger_id, item_id),
            ).fetchone()
        return row is not None

    def mark_rss_seen(self, trigger_id: str, item_id: str) -> None:
        import datetime
        with _lock:
            with self._conn:
                self._conn.execute(
                    "INSERT OR IGNORE INTO rss_seen (trigger_id, item_id, seen_at) "
                    "VALUES (?, ?, ?)",
                    (trigger_id, item_id, datetime.datetime.utcnow().isoformat()),
                )

    def clear_rss_seen(self, trigger_id: str) -> int:
        with _lock:
            with self._conn:
                cur = self._conn.execute(
                    "DELETE FROM rss_seen WHERE trigger_id=?", (trigger_id,)
                )
        return cur.rowcount

    def delete_runs_for_workflow(self, workflow_path: str, statuses: list[str] | None = None) -> int:
        with _lock:
            with self._conn:
                if statuses:
                    placeholders = ",".join("?" for _ in statuses)
                    cur = self._conn.execute(
                        f"DELETE FROM workflow_runs WHERE workflow_path=? AND status IN ({placeholders})",
                        (workflow_path, *statuses),
                    )
                else:
                    cur = self._conn.execute(
                        "DELETE FROM workflow_runs WHERE workflow_path=?", (workflow_path,)
                    )
                deleted = cur.rowcount
                self._conn.execute(
                    "DELETE FROM step_runs WHERE run_id NOT IN (SELECT id FROM workflow_runs)"
                )
        return deleted

    def cleanup_old_runs(self, days: int = 30) -> int:
        import datetime
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).isoformat()
        with _lock:
            with self._conn:
                cur = self._conn.execute(
                    "DELETE FROM workflow_runs WHERE finished_at < ? AND status IN ('completed', 'failed', 'cancelled')",
                    (cutoff,),
                )
                deleted = cur.rowcount
                self._conn.execute(
                    "DELETE FROM step_runs WHERE run_id NOT IN (SELECT id FROM workflow_runs)"
                )
        return deleted

    def delete_step_runs(self, run_id: str, step_ids: list[str]) -> int:
        if not step_ids:
            return 0
        placeholders = ",".join("?" for _ in step_ids)
        with _lock:
            with self._conn:
                cur = self._conn.execute(
                    f"DELETE FROM step_runs WHERE run_id=? AND step_id IN ({placeholders})",
                    (run_id, *step_ids),
                )
                return cur.rowcount

    def get_step_outputs(self, run_id: str) -> dict[str, Any]:
        step_runs = self.list_step_runs(run_id)
        outputs: dict[str, Any] = {}
        for sr in step_runs:
            if sr.status == StepRunStatus.completed and sr.output is not None:
                outputs[sr.step_id] = sr.output
        return outputs

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> WorkflowRun:
        payload = {}
        try:
            payload = json.loads(row["trigger_payload"])
        except (json.JSONDecodeError, TypeError):
            pass
        return WorkflowRun(
            id=row["id"],
            workflow_path=row["workflow_path"],
            trigger_id=row["trigger_id"],
            trigger_type=TriggerType(row["trigger_type"]),
            trigger_payload=payload,
            status=RunStatus(row["status"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            current_step=row["current_step"],
            error=row["error"],
        )

    @staticmethod
    def _row_to_step_run(row: sqlite3.Row) -> StepRun:
        input_resolved = None
        if row["input_resolved"]:
            try:
                input_resolved = json.loads(row["input_resolved"])
            except (json.JSONDecodeError, TypeError):
                pass
        output = None
        if row["output"] is not None:
            try:
                output = json.loads(row["output"])
            except (json.JSONDecodeError, TypeError):
                output = row["output"]
        return StepRun(
            run_id=row["run_id"],
            step_id=row["step_id"],
            status=StepRunStatus(row["status"]),
            input_resolved=input_resolved,
            output=output,
            error=row["error"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )
