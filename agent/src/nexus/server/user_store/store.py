from __future__ import annotations

import logging
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

from .models import Invite, Role, User, UserStatus
from .schema import init_schema

log = logging.getLogger(__name__)

_DB_PATH = Path.home() / ".nexus" / "server.sqlite"


class UserStore:
    def __init__(self, db_path: Path = _DB_PATH) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        init_schema(self._db)
        self._db.commit()

    def _row_to_user(self, row: sqlite3.Row) -> User:
        return User(
            id=row["id"],
            email=row["email"],
            display_name=row["display_name"],
            role=row["role"],
            status=row["status"],
            created_at=row["created_at"],
            last_login=row["last_login"],
            created_by=row["created_by"],
            nexus_uid=row["nexus_uid"] if "nexus_uid" in row.keys() else None,
        )

    def _row_to_invite(self, row: sqlite3.Row) -> Invite:
        return Invite(
            code=row["code"],
            created_by=row["created_by"],
            email=row["email"],
            role=row["role"],
            max_uses=row["max_uses"],
            use_count=row["use_count"],
            expires_at=row["expires_at"],
            created_at=row["created_at"],
        )

    def has_any_users(self) -> bool:
        row = self._db.execute("SELECT 1 FROM users LIMIT 1").fetchone()
        return row is not None

    def create_user(
        self,
        *,
        email: str,
        display_name: str,
        role: Role = "member",
        status: UserStatus = "active",
        created_by: str | None = None,
        nexus_uid: str | None = None,
    ) -> User:
        user_id = secrets.token_urlsafe(16)
        now = time.time()
        self._db.execute(
            "INSERT INTO users (id, email, display_name, role, status, nexus_uid, created_at, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, email, display_name, role, status, nexus_uid, now, created_by),
        )
        self._db.commit()
        return User(
            id=user_id,
            email=email,
            display_name=display_name,
            role=role,
            status=status,
            created_at=now,
            created_by=created_by,
            nexus_uid=nexus_uid,
        )

    def get_user(self, user_id: str) -> User | None:
        row = self._db.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return self._row_to_user(row) if row else None

    def get_user_by_email(self, email: str) -> User | None:
        row = self._db.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        return self._row_to_user(row) if row else None

    def get_user_by_nexus_uid(self, nexus_uid: str) -> User | None:
        row = self._db.execute(
            "SELECT * FROM users WHERE nexus_uid = ?", (nexus_uid,)
        ).fetchone()
        return self._row_to_user(row) if row else None

    def list_users(self) -> list[User]:
        rows = self._db.execute(
            "SELECT * FROM users ORDER BY created_at ASC"
        ).fetchall()
        return [self._row_to_user(r) for r in rows]

    def update_user(self, user_id: str, **fields: Any) -> User | None:
        allowed = {"display_name", "role", "status", "last_login"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_user(user_id)
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [user_id]
        self._db.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)
        self._db.commit()
        return self.get_user(user_id)

    def touch_login(self, user_id: str) -> None:
        self._db.execute(
            "UPDATE users SET last_login = ? WHERE id = ?",
            (time.time(), user_id),
        )
        self._db.commit()

    def create_invite(
        self,
        *,
        created_by: str,
        email: str | None = None,
        role: Role = "member",
        max_uses: int = 1,
        expires_at: float | None = None,
    ) -> Invite:
        code = secrets.token_urlsafe(24)
        now = time.time()
        self._db.execute(
            "INSERT INTO invites (code, created_by, email, role, max_uses, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (code, created_by, email, role, max_uses, expires_at, now),
        )
        self._db.commit()
        return Invite(
            code=code,
            created_by=created_by,
            email=email,
            role=role,
            max_uses=max_uses,
            expires_at=expires_at,
            created_at=now,
        )

    def get_invite(self, code: str) -> Invite | None:
        row = self._db.execute(
            "SELECT * FROM invites WHERE code = ?", (code,)
        ).fetchone()
        return self._row_to_invite(row) if row else None

    def validate_invite(self, code: str) -> tuple[bool, str]:
        invite = self.get_invite(code)
        if invite is None:
            return False, "Invalid invite code"
        if invite.use_count >= invite.max_uses:
            return False, "Invite has already been used"
        if invite.expires_at is not None and time.time() > invite.expires_at:
            return False, "Invite has expired"
        return True, ""

    def redeem_invite(
        self, code: str, email: str, display_name: str, *, nexus_uid: str | None = None,
    ) -> User:
        valid, err = self.validate_invite(code)
        if not valid:
            raise ValueError(err)
        invite = self.get_invite(code)
        assert invite is not None
        if invite.email is not None and invite.email.lower() != email.lower():
            raise ValueError("Email does not match invite")
        user = self.create_user(
            email=email,
            display_name=display_name,
            role=invite.role,
            created_by=invite.created_by,
            nexus_uid=nexus_uid,
        )
        self._db.execute(
            "UPDATE invites SET use_count = use_count + 1 WHERE code = ?",
            (code,),
        )
        self._db.commit()
        return user

    def list_invites(self, created_by: str | None = None) -> list[Invite]:
        if created_by:
            rows = self._db.execute(
                "SELECT * FROM invites WHERE created_by = ? ORDER BY created_at DESC",
                (created_by,),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM invites ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_invite(r) for r in rows]

    def revoke_invite(self, code: str) -> bool:
        cursor = self._db.execute("DELETE FROM invites WHERE code = ?", (code,))
        self._db.commit()
        return cursor.rowcount > 0

    def delete_user(self, user_id: str) -> bool:
        cursor = self._db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self._db.commit()
        return cursor.rowcount > 0

    def claim_session(self, session_id: str, user_id: str) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO session_owners (session_id, user_id) VALUES (?, ?)",
            (session_id, user_id),
        )
        self._db.commit()

    def session_owner(self, session_id: str) -> str | None:
        row = self._db.execute(
            "SELECT user_id FROM session_owners WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row["user_id"] if row else None

    def delete_session_owner(self, session_id: str) -> None:
        self._db.execute(
            "DELETE FROM session_owners WHERE session_id = ?", (session_id,)
        )
        self._db.commit()

    def create_shared_resource(self, path: str, owner_id: str) -> dict[str, Any]:
        rid = secrets.token_urlsafe(12)
        now = time.time()
        self._db.execute(
            "INSERT INTO shared_resources (id, path, owner_id, created_at) VALUES (?, ?, ?, ?)",
            (rid, path, owner_id, now),
        )
        self._db.commit()
        return {"id": rid, "path": path, "owner_id": owner_id, "created_at": now}

    def delete_shared_resource(self, path: str) -> bool:
        self._db.execute("DELETE FROM resource_acl WHERE resource_path = ?", (path,))
        cursor = self._db.execute(
            "DELETE FROM shared_resources WHERE path = ?", (path,)
        )
        self._db.commit()
        return cursor.rowcount > 0

    def list_shared_resources(self) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM shared_resources ORDER BY path ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def set_acl(
        self,
        resource_path: str,
        grantee_type: str,
        grantee_id: str,
        access_level: str,
        granted_by: str,
    ) -> None:
        aid = secrets.token_urlsafe(12)
        now = time.time()
        self._db.execute(
            "INSERT INTO resource_acl (id, resource_path, grantee_type, grantee_id, access_level, granted_by, granted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(resource_path, grantee_type, grantee_id) "
            "DO UPDATE SET access_level=excluded.access_level, granted_by=excluded.granted_by, granted_at=excluded.granted_at",
            (aid, resource_path, grantee_type, grantee_id, access_level, granted_by, now),
        )
        self._db.commit()

    def remove_acl(self, resource_path: str, grantee_type: str, grantee_id: str) -> bool:
        cursor = self._db.execute(
            "DELETE FROM resource_acl WHERE resource_path = ? AND grantee_type = ? AND grantee_id = ?",
            (resource_path, grantee_type, grantee_id),
        )
        self._db.commit()
        return cursor.rowcount > 0

    def list_acl(self, resource_path: str) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM resource_acl WHERE resource_path = ? ORDER BY grantee_type, grantee_id",
            (resource_path,),
        ).fetchall()
        return [dict(r) for r in rows]

    def check_access(self, resource_path: str, user_id: str, role: str, need: str = "read") -> bool:
        rows = self._db.execute(
            "SELECT grantee_type, grantee_id, access_level FROM resource_acl WHERE resource_path = ?",
            (resource_path,),
        ).fetchall()
        _LEVELS = {"read": 0, "write": 1}
        need_level = _LEVELS.get(need, 0)
        for r in rows:
            if r["grantee_type"] == "role" and r["grantee_id"] == role:
                if _LEVELS.get(r["access_level"], 0) >= need_level:
                    return True
            if r["grantee_type"] == "user" and r["grantee_id"] == user_id:
                if _LEVELS.get(r["access_level"], 0) >= need_level:
                    return True
        return False

    def shared_resources_for_user(self, user_id: str, role: str) -> list[dict[str, Any]]:
        resources = self.list_shared_resources()
        result = []
        for r in resources:
            if self.check_access(r["path"], user_id, role):
                result.append(r)
        return result

    def close(self) -> None:
        self._db.close()
