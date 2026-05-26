"""Webhook registry — SQLite-backed log of all broker endpoints.

Tracks every broker webhook created by this Nexus instance so we can:
  * Reconcile on startup (detect orphans, recreate missing ones).
  * Evict stale endpoints when the user exceeds their plan limit.
  * Clean up when kanban boards / workflows are deleted.
  * Maintain a pool of unassigned webhooks for quick assignment.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..sqlite_base import SqliteStore

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS broker_webhooks (
    broker_id TEXT PRIMARY KEY,
    broker_slug TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    endpoint_type TEXT,
    endpoint_key TEXT,
    local_token TEXT,
    vault_path TEXT,
    created_at TEXT NOT NULL,
    last_verified_at TEXT,
    last_error TEXT,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_bw_type ON broker_webhooks(endpoint_type);
CREATE INDEX IF NOT EXISTS idx_bw_vault ON broker_webhooks(vault_path);
CREATE INDEX IF NOT EXISTS idx_bw_active ON broker_webhooks(is_active);
"""


class WebhookRegistry(SqliteStore):
    _SCHEMA = _SCHEMA

    def register(
        self,
        *,
        broker_id: str,
        broker_slug: str,
        name: str,
        endpoint_type: str | None = None,
        endpoint_key: str | None = None,
        local_token: str | None = None,
        vault_path: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO broker_webhooks "
                "(broker_id, broker_slug, name, endpoint_type, endpoint_key, "
                "local_token, vault_path, created_at, last_verified_at, is_active) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (broker_id, broker_slug, name, endpoint_type, endpoint_key,
                 local_token, vault_path, now, now),
            )

    def assign(
        self,
        broker_id: str,
        *,
        endpoint_type: str,
        endpoint_key: str,
        local_token: str,
        vault_path: str,
    ) -> bool:
        with self._db:
            cur = self._db.execute(
                "UPDATE broker_webhooks SET "
                "endpoint_type=?, endpoint_key=?, local_token=?, vault_path=? "
                "WHERE broker_id=? AND (endpoint_type IS NULL OR endpoint_type = '')",
                (endpoint_type, endpoint_key, local_token, vault_path, broker_id),
            )
            return cur.rowcount > 0

    def unassign(self, broker_id: str) -> bool:
        with self._db:
            cur = self._db.execute(
                "UPDATE broker_webhooks SET "
                "endpoint_type=NULL, endpoint_key=NULL, local_token=NULL, vault_path=NULL "
                "WHERE broker_id=?",
                (broker_id,),
            )
            return cur.rowcount > 0

    def mark_verified(self, broker_id: str, *, error: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._db:
            self._db.execute(
                "UPDATE broker_webhooks SET last_verified_at=?, last_error=?, is_active=1 "
                "WHERE broker_id=?",
                (now, error, broker_id),
            )

    def mark_gone(self, broker_id: str) -> None:
        with self._db:
            self._db.execute(
                "UPDATE broker_webhooks SET is_active=0 WHERE broker_id=?",
                (broker_id,),
            )

    def remove(self, broker_id: str) -> None:
        with self._db:
            self._db.execute(
                "DELETE FROM broker_webhooks WHERE broker_id=?",
                (broker_id,),
            )

    def remove_by_vault_path(self, vault_path: str) -> list[dict[str, Any]]:
        with self._db:
            rows = self._db.execute(
                "SELECT * FROM broker_webhooks WHERE vault_path=?",
                (vault_path,),
            ).fetchall()
            self._db.execute(
                "DELETE FROM broker_webhooks WHERE vault_path=?",
                (vault_path,),
            )
        return [dict(r) for r in rows]

    def remove_by_key(self, endpoint_type: str, endpoint_key: str) -> dict[str, Any] | None:
        with self._db:
            row = self._db.execute(
                "SELECT * FROM broker_webhooks WHERE endpoint_type=? AND endpoint_key=?",
                (endpoint_type, endpoint_key),
            ).fetchone()
            if row is None:
                return None
            self._db.execute(
                "DELETE FROM broker_webhooks WHERE broker_id=?",
                (row["broker_id"],),
            )
        return dict(row)

    def get(self, broker_id: str) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT * FROM broker_webhooks WHERE broker_id=?",
            (broker_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_by_key(self, endpoint_type: str, endpoint_key: str) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT * FROM broker_webhooks WHERE endpoint_type=? AND endpoint_key=?",
            (endpoint_type, endpoint_key),
        ).fetchone()
        return dict(row) if row else None

    def list_all(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        if active_only:
            rows = self._db.execute(
                "SELECT * FROM broker_webhooks WHERE is_active=1"
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM broker_webhooks"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_unassigned(self) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM broker_webhooks WHERE (endpoint_type IS NULL OR endpoint_type = '') AND is_active=1"
        ).fetchall()
        return [dict(r) for r in rows]

    def count_active(self) -> int:
        row = self._db.execute(
            "SELECT COUNT(*) as cnt FROM broker_webhooks WHERE is_active=1"
        ).fetchone()
        return row["cnt"] if row else 0

    def count_assigned(self) -> int:
        row = self._db.execute(
            "SELECT COUNT(*) as cnt FROM broker_webhooks WHERE is_active=1 AND endpoint_type IS NOT NULL AND endpoint_type != ''"
        ).fetchone()
        return row["cnt"] if row else 0


_registry: WebhookRegistry | None = None


def get_registry() -> WebhookRegistry:
    global _registry
    if _registry is None:
        from ..home import nexus_dir
        db_path = nexus_dir() / "broker_webhooks.db"
        _registry = WebhookRegistry(db_path)
    return _registry
