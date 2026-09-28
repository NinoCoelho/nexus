"""TelegramBindingStore — (chat_id, thread_id) → project + active session.

Follows the ProjectStore pattern: short-lived sqlite3 connections against
the sessions DB (``~/.nexus/sessions.sqlite``), table created by the
``_TELEGRAM_SCHEMA`` migration in ``session_store/schema.py``.

Mapping model ("topic = project"):
- DM:            chat_id = user id, thread_id = 0, kind='dm', project_id NULL.
- Plain group:   chat_id = group id, thread_id = 0, kind='group', one project.
- Forum topic:   chat_id = group id, thread_id = message_thread_id,
                 kind='topic', each topic bound to its own project.

Each binding tracks the *active* session; additional chats for the same
project are regular sessions with ``sessions.project_id`` set — they show
up in the UI sidebar under the project automatically.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..home import sessions_db

log = logging.getLogger(__name__)


@dataclass
class TelegramBinding:
    chat_id: int
    thread_id: int
    kind: str  # 'dm' | 'group' | 'topic'
    project_id: str | None
    active_session_id: str


class TelegramBindingStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or sessions_db()

    def _connect(self) -> Any:
        import sqlite3

        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, chat_id: int, thread_id: int = 0) -> TelegramBinding | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM telegram_bindings WHERE chat_id = ? AND thread_id = ?",
                (chat_id, thread_id),
            ).fetchone()
        finally:
            conn.close()
        return self._row_to_binding(row) if row else None

    def upsert(
        self,
        *,
        chat_id: int,
        thread_id: int = 0,
        kind: str,
        project_id: str | None,
        active_session_id: str,
    ) -> TelegramBinding:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO telegram_bindings
                    (chat_id, thread_id, kind, project_id, active_session_id)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, thread_id) DO UPDATE SET
                    kind = excluded.kind,
                    project_id = excluded.project_id,
                    active_session_id = excluded.active_session_id,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (chat_id, thread_id, kind, project_id, active_session_id),
            )
            conn.commit()
        finally:
            conn.close()
        return TelegramBinding(
            chat_id=chat_id,
            thread_id=thread_id,
            kind=kind,
            project_id=project_id,
            active_session_id=active_session_id,
        )

    def set_active_session(
        self, chat_id: int, thread_id: int, session_id: str
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE telegram_bindings SET active_session_id = ?, "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE chat_id = ? AND thread_id = ?",
                (session_id, chat_id, thread_id),
            )
            conn.commit()
        finally:
            conn.close()

    def set_project(
        self, chat_id: int, thread_id: int, project_id: str | None
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE telegram_bindings SET project_id = ?, "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE chat_id = ? AND thread_id = ?",
                (project_id, chat_id, thread_id),
            )
            conn.commit()
        finally:
            conn.close()

    def delete(self, chat_id: int, thread_id: int = 0) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "DELETE FROM telegram_bindings WHERE chat_id = ? AND thread_id = ?",
                (chat_id, thread_id),
            )
            conn.commit()
        finally:
            conn.close()

    def find_by_session(self, session_id: str) -> TelegramBinding | None:
        """Locate the binding whose *active* session is ``session_id``.

        Used by the HITL forwarder to route ask_user prompts for
        Telegram-created sessions back to the right chat/thread.
        """
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM telegram_bindings WHERE active_session_id = ?",
                (session_id,),
            ).fetchone()
        finally:
            conn.close()
        return self._row_to_binding(row) if row else None

    def _row_to_binding(self, row: Any) -> TelegramBinding:
        return TelegramBinding(
            chat_id=row["chat_id"],
            thread_id=row["thread_id"],
            kind=row["kind"],
            project_id=row["project_id"],
            active_session_id=row["active_session_id"],
        )


def session_is_telegram_routed(session_id: str) -> bool:
    """True when ``session_id`` is a Telegram binding's active session.

    Used to suppress duplicate HITL prompts in the web UI (dialog,
    pending recovery, web push) — the Telegram forwarder owns those
    prompts. Best-effort: any failure reads as "not routed" so prompts
    still surface somewhere.
    """
    try:
        return TelegramBindingStore().find_by_session(session_id) is not None
    except Exception:
        log.debug("telegram: routing check failed", exc_info=True)
        return False
