from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Role = Literal["admin", "member", "viewer"]
UserStatus = Literal["active", "suspended", "pending"]


@dataclass
class User:
    id: str
    email: str
    display_name: str
    role: Role
    status: UserStatus
    created_at: float
    last_login: float | None = None
    created_by: str | None = None
    nexus_uid: str | None = None


@dataclass
class Invite:
    code: str
    created_by: str
    email: str | None = None
    role: Role = "member"
    max_uses: int = 1
    use_count: int = 0
    expires_at: float | None = None
    created_at: float = 0.0
