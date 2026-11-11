"""FTS5 index over compaction archive records (``.parts`` JSONL).

Compaction is lossy for the context window but never for the record: every
summarized/elided batch of messages is journaled verbatim to
``~/.nexus/vault/.session-memory/.parts/<session_id>.jsonl`` (see
:func:`nexus.agent.loop.summarize.persist_summary_part`). Until now that
archive had no reader — nothing the *agent* could call. This module gives the
archive a search index so ``history_recall`` can find and fetch what
compaction removed, across every session that ever existed.

Design notes:

- The JSONL files stay the source of truth. This index is *derived* state:
  rebuildable from the files, deleted alongside them (the
  ``context_cleanup`` heartbeat driver and ``purge_session_artifacts`` both
  call :func:`remove_session` / ``remove_file_rows``).
- ``parts_fts`` is a standalone FTS5 table (not an external-content one like
  ``messages_fts``) because archive records have no base table to mirror.
  Coordinate columns (``session_id``, ``line_no``, ``msg_idx``) are UNINDEXED
  payloads that ``history_recall`` turns into ``part:`` refs.
- Backfill is incremental: ``parts_meta`` tracks how many JSONL lines of each
  session file have been indexed; a larger file on disk means new records to
  index. Runs lazily on first search and is cheap afterwards.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Live SessionStore, injected at server startup (same pattern as
# ``context_tool.set_session_store``: the tool handlers are sync and
# module-level, so the store arrives by side door rather than by argument).
_STORE_REF: Any = None
_lock = threading.Lock()
_backfill_done = False


def set_parts_store(store: Any) -> None:
    """Wire the live session store (called next to the context-tool wiring)."""
    global _STORE_REF
    _STORE_REF = store


def _db() -> Any:
    store = _STORE_REF
    if store is None:
        return None
    try:
        return store._loom._db  # noqa: SLF001 — same access every mixin uses
    except AttributeError:
        return None


_PARTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS parts_fts USING fts5(
    content,
    session_id UNINDEXED,
    line_no UNINDEXED,
    msg_idx UNINDEXED,
    role UNINDEXED,
    ts UNINDEXED,
    reason UNINDEXED
);
CREATE TABLE IF NOT EXISTS parts_meta (
    session_id TEXT PRIMARY KEY,
    line_count INTEGER NOT NULL DEFAULT 0
);
"""


def _ensure(db: Any) -> None:
    db.executescript(_PARTS_SCHEMA)


def _store_lock() -> Any:
    store = _STORE_REF
    lock = getattr(store, "_lock", None) if store is not None else None
    return lock if lock is not None else threading.Lock()


# ── write path ───────────────────────────────────────────────────────────────


def index_record(session_id: str, path: Path | str, line_no: int, record: dict) -> None:
    """Index the messages of one freshly-appended archive record.

    Called from ``persist_summary_part`` right after the JSONL line is
    written. ``line_no`` is the 1-based JSONL line index; ``msg_idx`` the
    position of the message inside the record's ``messages`` array. Together
    with ``session_id`` they form the ``part:{sid}:{line}:{idx}`` ref that
    ``history_recall get`` resolves back to the verbatim message.
    """
    db = _db()
    if db is None:
        return
    ts = str(record.get("ts") or "")
    reason = str(record.get("reason") or "")
    messages = record.get("messages") or []
    lock = _store_lock()
    try:
        with lock:
            _ensure(db)
            for idx, msg in enumerate(messages):
                content = msg.get("content") if isinstance(msg, dict) else None
                if isinstance(content, list):
                    # Multipart content — index its text parts, store nothing
                    # oversized; get() re-reads the verbatim record from disk.
                    content = " ".join(
                        str(p.get("text") or "")
                        for p in content
                        if isinstance(p, dict) and p.get("kind") == "text"
                    )
                if not isinstance(content, str) or not content.strip():
                    continue
                db.execute(
                    "INSERT INTO parts_fts(content, session_id, line_no, msg_idx, role, ts, reason) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        content,
                        session_id,
                        line_no,
                        idx,
                        str(msg.get("role") or ""),
                        ts,
                        reason,
                    ),
                )
            db.execute(
                "INSERT INTO parts_meta(session_id, line_count) VALUES (?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET line_count = excluded.line_count",
                (session_id, line_no),
            )
            db.commit()
    except Exception:  # noqa: BLE001 — indexing must never break compaction
        log.debug("parts_fts: index_record failed for %s:%s", session_id, line_no, exc_info=True)


def remove_session(session_id: str) -> None:
    """Drop every archive-index row for one session (used by purge)."""
    db = _db()
    if db is None:
        return
    try:
        with _store_lock():
            db.execute("DELETE FROM parts_fts WHERE session_id = ?", (session_id,))
            db.execute("DELETE FROM parts_meta WHERE session_id = ?", (session_id,))
            db.commit()
    except Exception:  # noqa: BLE001
        log.debug("parts_fts: remove_session failed for %s", session_id, exc_info=True)


def remove_file_rows(path: Path | str) -> None:
    """Drop index rows whose archive *file* was deleted by the retention sweep."""
    p = Path(path)
    remove_session(p.stem)


# ── backfill ─────────────────────────────────────────────────────────────────


def backfill() -> int:
    """Index archive records not yet in the index. Returns rows added.

    Incremental per session via ``parts_meta.line_count``, so the common case
    after the first run is one file with a few new lines (or nothing).
    """
    global _backfill_done
    db = _db()
    if db is None:
        return 0
    try:
        from nexus.home import vault_session_memory

        parts_dir = vault_session_memory() / ".parts"
    except Exception:  # noqa: BLE001
        return 0
    if not parts_dir.is_dir():
        return 0

    added = 0
    with _lock:  # serialize backfills against each other, not against reads
        try:
            with _store_lock():
                _ensure(db)
                done = {
                    row[0]: int(row[1] or 0)
                    for row in db.execute("SELECT session_id, line_count FROM parts_meta").fetchall()
                }
            for fp in sorted(parts_dir.glob("*.jsonl")):
                sid = fp.stem
                try:
                    with fp.open("r", encoding="utf-8") as fh:
                        lines = fh.readlines()
                except OSError:
                    continue
                start = done.get(sid, 0)
                if start >= len(lines):
                    continue
                with _store_lock():
                    for offset, raw in enumerate(lines[start:], start=start + 1):
                        try:
                            record = json.loads(raw)
                        except (ValueError, TypeError):
                            continue
                        messages = record.get("messages") or []
                        ts = str(record.get("ts") or "")
                        reason = str(record.get("reason") or "")
                        for idx, msg in enumerate(messages):
                            content = msg.get("content") if isinstance(msg, dict) else None
                            if isinstance(content, list):
                                content = " ".join(
                                    str(p.get("text") or "")
                                    for p in content
                                    if isinstance(p, dict) and p.get("kind") == "text"
                                )
                            if not isinstance(content, str) or not content.strip():
                                continue
                            db.execute(
                                "INSERT INTO parts_fts(content, session_id, line_no, msg_idx, role, ts, reason) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (content, sid, offset, idx, str(msg.get("role") or ""), ts, reason),
                            )
                            added += 1
                        db.execute(
                            "INSERT INTO parts_meta(session_id, line_count) VALUES (?, ?) "
                            "ON CONFLICT(session_id) DO UPDATE SET line_count = excluded.line_count",
                            (sid, len(lines)),
                        )
                    db.commit()
            _backfill_done = True
        except Exception:  # noqa: BLE001 — a failed backfill must not block search
            log.debug("parts_fts: backfill failed", exc_info=True)
    if added:
        log.info("parts_fts: backfilled %d archived message(s)", added)
    return added


# ── read path ────────────────────────────────────────────────────────────────


def search(
    query: str,
    *,
    limit: int = 8,
    role: str | None = None,
    since: str | None = None,
) -> list[dict]:
    """BM25-ranked search over the archive index.

    Returns ``[{"ref", "session_id", "ts", "role", "reason", "snippet",
    "score"}]``. Never raises — a missing/malformed index degrades to [].
    """
    db = _db()
    if db is None or not query.strip():
        return []
    sql = (
        "SELECT session_id, line_no, msg_idx, role, ts, reason, "
        "snippet(parts_fts, 0, '[[', ']]', ' … ', 12) AS snip, "
        "bm25(parts_fts) AS score "
        "FROM parts_fts WHERE parts_fts MATCH ?"
    )
    params: list[Any] = [query]
    if role:
        sql += " AND role = ?"
        params.append(role)
    if since:
        sql += " AND ts >= ?"
        params.append(since)
    sql += " ORDER BY score LIMIT ?"
    params.append(limit * 3)  # overfetch — scope/dedupe trims below
    try:
        if not _backfill_done:
            backfill()
        with _store_lock():
            rows = db.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for r in rows:
        sid, line_no, msg_idx, m_role, ts, reason, snip, score = r
        out.append({
            "ref": f"part:{sid}:{line_no}:{msg_idx}",
            "session_id": sid,
            "ts": ts or "",
            "role": m_role or "",
            "reason": reason or "",
            "snippet": (snip or "")[:400],
            "score": score,
        })
    return out


def fetch(session_id: str, line_no: int, msg_idx: int) -> dict | None:
    """Resolve a ``part:`` ref to the verbatim message, re-reading the JSONL.

    The archive file is the source of truth; the index only points here.
    """
    try:
        from nexus.home import vault_session_memory

        path = vault_session_memory() / ".parts" / f"{session_id}.jsonl"
        if not path.is_file():
            return None
        with path.open("r", encoding="utf-8") as fh:
            for i, raw in enumerate(fh, start=1):
                if i != line_no:
                    continue
                record = json.loads(raw)
                messages = record.get("messages") or []
                if 0 <= msg_idx < len(messages):
                    msg = messages[msg_idx]
                    return {
                        "session_id": session_id,
                        "ts": str(record.get("ts") or ""),
                        "reason": str(record.get("reason") or ""),
                        "message": msg,
                    }
                return None
    except Exception:  # noqa: BLE001 — a bad ref is an empty result, not an error
        log.debug("parts_fts: fetch failed for %s:%s:%s", session_id, line_no, msg_idx, exc_info=True)
    return None
