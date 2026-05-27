"""Tiny SQLite base class for Nexus stores.

Each store creates its own DB file under ``~/.nexus/`` with the same
boilerplate: ``mkdir -p``, ``connect``, ``PRAGMA journal_mode=WAL``,
``CREATE TABLE IF NOT EXISTS``, ``commit``. This mixin removes that
repetition.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


class SqliteStore:
    """Base class that handles DB bootstrap.

    Subclasses set ``_SCHEMA`` (a string of SQL DDL) and call
    ``super().__init__(db_path)``. The connection is available as
    ``self._db``.
    """

    _SCHEMA: str = ""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        if self._SCHEMA:
            self._db.executescript(self._SCHEMA)
        self._db.commit()

    def close(self) -> None:
        try:
            self._db.close()
        except Exception:
            pass
