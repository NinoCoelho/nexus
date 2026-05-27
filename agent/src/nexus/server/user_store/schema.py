from __future__ import annotations

import sqlite3


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    display_name  TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'member',
    status        TEXT NOT NULL DEFAULT 'active',
    nexus_uid     TEXT UNIQUE,
    created_at    REAL NOT NULL,
    last_login    REAL,
    created_by    TEXT
);

CREATE TABLE IF NOT EXISTS invites (
    code        TEXT PRIMARY KEY,
    created_by  TEXT NOT NULL,
    email       TEXT,
    role        TEXT NOT NULL DEFAULT 'member',
    max_uses    INTEGER NOT NULL DEFAULT 1,
    use_count   INTEGER NOT NULL DEFAULT 0,
    expires_at  REAL,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS session_owners (
    session_id TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shared_resources (
    id         TEXT PRIMARY KEY,
    path       TEXT NOT NULL UNIQUE,
    owner_id   TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS resource_acl (
    id            TEXT PRIMARY KEY,
    resource_path TEXT NOT NULL,
    grantee_type  TEXT NOT NULL,
    grantee_id    TEXT NOT NULL,
    access_level  TEXT NOT NULL DEFAULT 'read',
    granted_by    TEXT NOT NULL,
    granted_at    REAL NOT NULL,
    UNIQUE(resource_path, grantee_type, grantee_id)
);
"""


def init_schema(db: sqlite3.Connection) -> None:
    db.executescript(_SCHEMA)
    _migrate_nexus_uid(db)


def _migrate_nexus_uid(db: sqlite3.Connection) -> None:
    cols = [r[1] for r in db.execute("PRAGMA table_info(users)").fetchall()]
    if "nexus_uid" not in cols:
        db.execute("ALTER TABLE users ADD COLUMN nexus_uid TEXT")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_nexus_uid ON users(nexus_uid)")
    if "password_hash" in cols:
        db.execute("ALTER TABLE users DROP COLUMN password_hash")
