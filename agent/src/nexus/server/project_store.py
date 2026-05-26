"""ProjectStore — CRUD data access for the Projects feature.

Projects are named workspaces that group related chat sessions with
project-scoped instructions and an auto-created vault subfolder.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .session_store.models import _ts_to_int

log = logging.getLogger(__name__)


@dataclass
class Project:
    id: str
    name: str
    description: str
    instructions: str
    vault_path: str
    color: str
    icon: str
    created_at: int
    updated_at: int


@dataclass
class ProjectSummary:
    id: str
    name: str
    description: str
    color: str
    icon: str
    session_count: int
    created_at: int
    updated_at: int


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return slug[:60] or "project"


class ProjectStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def _connect(self) -> Any:
        import sqlite3

        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _unique_vault_path(self, conn: Any, name: str) -> str:
        base = _slugify(name)
        vault_path = f"projects/{base}"
        n = 1
        while conn.execute(
            "SELECT 1 FROM projects WHERE vault_path = ?", (vault_path,)
        ).fetchone():
            n += 1
            vault_path = f"projects/{base}-{n}"
        return vault_path

    def create(
        self,
        *,
        name: str,
        description: str = "",
        instructions: str = "",
        color: str = "",
        icon: str = "",
    ) -> Project:
        pid = uuid.uuid4().hex
        conn = self._connect()
        try:
            vault_path = self._unique_vault_path(conn, name)
            conn.execute(
                "INSERT INTO projects (id, name, description, instructions, vault_path, color, icon) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (pid, name, description, instructions, vault_path, color, icon),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ?", (pid,)
            ).fetchone()
        finally:
            conn.close()

        from ..home import vault_root

        folder = vault_root() / vault_path
        folder.mkdir(parents=True, exist_ok=True)
        readme = folder / "README.md"
        if not readme.exists():
            readme.write_text(f"# {name}\n\n{description}\n", encoding="utf-8")

        return self._row_to_project(row)

    def get(self, project_id: str) -> Project | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return self._row_to_project(row)

    def list(self, limit: int = 50) -> list[ProjectSummary]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT p.id, p.name, p.description, p.color, p.icon,
                       p.created_at, p.updated_at,
                       (SELECT COUNT(*) FROM sessions s WHERE s.project_id = p.id) AS session_count
                FROM projects p
                ORDER BY p.updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            conn.close()
        return [
            ProjectSummary(
                id=r["id"],
                name=r["name"],
                description=r["description"] or "",
                color=r["color"] or "",
                icon=r["icon"] or "",
                session_count=r["session_count"] or 0,
                created_at=_ts_to_int(r["created_at"]),
                updated_at=_ts_to_int(r["updated_at"]),
            )
            for r in rows
        ]

    def update(self, project_id: str, **fields: Any) -> None:
        allowed = {"name", "description", "instructions", "color", "icon"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return
        sets = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [project_id]
        conn = self._connect()
        try:
            conn.execute(
                f"UPDATE projects SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                values,
            )
            conn.commit()
        finally:
            conn.close()

    def delete(self, project_id: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE sessions SET project_id = NULL WHERE project_id = ?",
                (project_id,),
            )
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            conn.commit()
        finally:
            conn.close()

    def move_session(self, session_id: str, project_id: str | None) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE sessions SET project_id = ? WHERE id = ?",
                (project_id, session_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_project_for_session(self, session_id: str) -> Project | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT p.* FROM projects p "
                "JOIN sessions s ON s.project_id = p.id "
                "WHERE s.id = ?",
                (session_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return self._row_to_project(row)

    def _row_to_project(self, row: Any) -> Project:
        return Project(
            id=row["id"],
            name=row["name"],
            description=row["description"] or "",
            instructions=row["instructions"] or "",
            vault_path=row["vault_path"],
            color=row["color"] or "",
            icon=row["icon"] or "",
            created_at=_ts_to_int(row["created_at"]),
            updated_at=_ts_to_int(row["updated_at"]),
        )
