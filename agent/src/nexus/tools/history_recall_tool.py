"""``history_recall`` — agent-facing recall over conversation history.

Compaction (summarization, hard-trim elision, tool-result stubbing) removes
detail from the context window on purpose. This tool is the way to get it
back: it searches **all** past conversations (live history in the session
store, indexed via ``messages_fts``) plus the compaction archive
(``vault/.session-memory/.parts/``, indexed via ``parts_fts``) and returns
verbatim messages on demand.

This closes the loop the Codex-experimental architecture and MemGPT's
archival-memory design both converge on: keep the working window short,
make everything older retrievable by search instead of by summary.

Sources, by ref prefix:
- ``live:{session_id}:{seq}`` — a message currently (or formerly) stored for
  a session, fetched from the messages table.
- ``part:{session_id}:{line}:{idx}`` — a message that was compacted away,
  re-read verbatim from the ``.parts`` JSONL archive.

All-history scope is the point: a personal assistant that survived weeks of
chats should be able to answer "what was the URL we found in that research
last Tuesday?" without the user re-pasting anything.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from typing import Any

from ..agent.context import CURRENT_HISTORY, CURRENT_SESSION_ID
from ..agent.llm import ToolSpec
from ..agent.loop import parts_index

log = logging.getLogger(__name__)

# Live SessionStore, injected at server startup (app.py) — same pattern as
# context_tool / parts_index: sync module-level handler, store by side door.
_STORE_REF: Any = None


def set_session_store(store: Any) -> None:
    """Wire the live session store (also arms the parts index)."""
    global _STORE_REF
    _STORE_REF = store
    parts_index.set_parts_store(store)


HISTORY_RECALL_TOOL = ToolSpec(
    name="history_recall",
    description=(
        "Search past conversations and fetch verbatim messages. Covers ALL "
        "sessions (this chat and every older chat) plus everything that was "
        "summarized or trimmed away during compaction. Use before re-asking "
        "the user or redoing research — if it was ever said in a chat, this "
        "finds it. Actions: search (keyword over all history), get (verbatim "
        "message by ref), recent (last messages of this session verbatim)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "get", "recent"],
                "description": (
                    "search = full-text across all history; get = verbatim "
                    "message for one ref from a search result; recent = the "
                    "last N messages of the current session."
                ),
            },
            "query": {
                "type": "string",
                "description": "search: keywords (e.g. 'stripe api key pricing')",
            },
            "scope": {
                "type": "string",
                "enum": ["all", "session"],
                "description": "search: 'all' = every session (default), 'session' = this chat only",
            },
            "role": {
                "type": "string",
                "enum": ["user", "assistant", "tool", "any"],
                "description": "search: restrict to one message role",
            },
            "since": {
                "type": "string",
                "description": "search: only messages from this date onward (YYYY-MM-DD)",
            },
            "limit": {
                "type": "integer",
                "description": "search: max results (default 8, max 20)",
            },
            "ref": {
                "type": "string",
                "description": "get: a ref returned by search (live:... or part:...)",
            },
            "n": {
                "type": "integer",
                "description": "recent: how many messages (default 10, max 30)",
            },
        },
        "required": ["action"],
    },
)


def _current_session_id() -> str | None:
    try:
        return CURRENT_SESSION_ID.get() or None
    except LookupError:
        return None


def _escape_query(query: str) -> str:
    """Wrap every token in double-quotes so raw FTS5 operators are rejected."""
    tokens = re.findall(r"\S+", query)
    if not tokens:
        return '""'
    return " ".join(f'"{t}"' for t in tokens)


def _db() -> Any:
    store = _STORE_REF
    if store is None:
        return None
    try:
        return store._loom._db  # noqa: SLF001 — same access the mixins use
    except AttributeError:
        return None


def _store_lock() -> Any:
    lock = getattr(_STORE_REF, "_lock", None) if _STORE_REF is not None else None
    import threading

    return lock if lock is not None else threading.Lock()


_GET_CHAR_CAP = 16_000
_RECENT_MSG_CAP = 4_000

# Config gate, resolved lazily (config can change between calls).
def _enabled() -> bool:
    try:
        from ..config_file import load_cached

        return bool(getattr(getattr(load_cached(), "agent", None), "history_recall", True))
    except Exception:  # noqa: BLE001 — a config hiccup must not kill recall
        return True


# ── search ───────────────────────────────────────────────────────────────────


def _search_live(query: str, *, role: str | None, since: str | None, limit: int) -> list[dict]:
    db = _db()
    if db is None:
        return []
    sql = (
        "SELECT m.session_id, m.seq, m.role, m.created_at, s.title, s.hidden, "
        "snippet(messages_fts, 0, '[[', ']]', ' … ', 12) AS snip, "
        "bm25(messages_fts) AS score "
        "FROM messages_fts "
        "JOIN messages m ON m.rowid = messages_fts.rowid "
        "JOIN sessions s ON s.id = m.session_id "
        "WHERE messages_fts MATCH ?"
    )
    params: list[Any] = [query]
    if role:
        sql += " AND m.role = ?"
        params.append(role)
    if since:
        sql += " AND m.created_at >= ?"
        params.append(since)
    sql += " ORDER BY score LIMIT ?"
    params.append(limit * 3)
    try:
        with _store_lock():
            rows = db.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for sid, seq, m_role, created, title, hidden, snip, score in rows:
        out.append({
            "ref": f"live:{sid}:{seq}",
            "session_id": sid,
            "session_title": title or "New session",
            "ts": created or "",
            "role": m_role or "",
            "archived": False,
            "in_subagent": bool(hidden),
            "snippet": (snip or "")[:400],
            "score": score,
        })
    return out


def _search_parts(query: str, *, role: str | None, since: str | None, limit: int) -> list[dict]:
    rows = parts_index.search(query, limit=limit * 3, role=role, since=since)
    for r in rows:
        r["archived"] = True
        r.setdefault("session_title", "")
        r.setdefault("in_subagent", False)
    return rows


def _handle_search(args: dict[str, Any]) -> str:
    raw_query = str(args.get("query") or "").strip()
    if not raw_query:
        return json.dumps(
            {"ok": False, "error": "search needs a 'query' (keywords to look for)"},
            ensure_ascii=False,
        )
    query = _escape_query(raw_query)
    role = args.get("role") or None
    if role in ("any", "", None):
        role = None
    since = str(args.get("since") or "").strip() or None
    try:
        limit = max(1, min(int(args.get("limit") or 8), 20))
    except (TypeError, ValueError):
        limit = 8
    scope = args.get("scope") or "all"
    sid = _current_session_id()

    live = _search_live(query, role=role, since=since, limit=limit)
    parts = _search_parts(query, role=role, since=since, limit=limit)
    merged = sorted(live + parts, key=lambda r: r["score"])
    if scope == "session" and sid:
        merged = [r for r in merged if r["session_id"] == sid]
    # Trim to limit, keeping a mix of live and archived hits rather than
    # letting whichever source ranked first crowd the other out entirely.
    seen_sessions: dict[str, int] = {}
    results = []
    for r in merged:
        per_session = seen_sessions.get(r["session_id"], 0)
        if per_session >= max(2, limit // 2):
            continue
        r.pop("score", None)
        results.append(r)
        seen_sessions[r["session_id"]] = per_session + 1
        if len(results) >= limit:
            break

    return json.dumps({
        "ok": True,
        "query": raw_query,
        "scope": scope,
        "count": len(results),
        "results": results,
        "hint": (
            "Use action=get with a result's ref to read the full verbatim "
            "message. 'part:' refs come from the compaction archive (messages "
            "that were summarized/trimmed out of some session's window)."
        ),
    }, ensure_ascii=False)


# ── get ──────────────────────────────────────────────────────────────────────


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(p.get("text") or "")
            for p in content
            if isinstance(p, dict) and p.get("kind") == "text"
        )
    return str(content or "")


def _handle_get(args: dict[str, Any]) -> str:
    ref = str(args.get("ref") or "").strip()
    if not ref:
        return json.dumps(
            {"ok": False, "error": "get needs a 'ref' from a previous search result"},
            ensure_ascii=False,
        )
    kind, _, rest = ref.partition(":")
    if kind == "live":
        sid, _, seq_s = rest.rpartition(":")
        try:
            seq = int(seq_s)
        except ValueError:
            return json.dumps({"ok": False, "error": f"malformed ref: {ref!r}"}, ensure_ascii=False)
        db = _db()
        if db is None:
            return json.dumps({"ok": False, "error": "history_recall unavailable (no store)"}, ensure_ascii=False)
        try:
            with _store_lock():
                row = db.execute(
                    "SELECT role, content, created_at FROM messages WHERE session_id = ? AND seq = ?",
                    (sid, seq),
                ).fetchone()
        except (sqlite3.OperationalError, TypeError):
            row = None
        if row is None:
            return json.dumps({"ok": False, "error": f"no message at {ref!r}"}, ensure_ascii=False)
        text = _content_text(row[1])
        truncated = len(text) > _GET_CHAR_CAP
        return json.dumps({
            "ok": True,
            "ref": ref,
            "session_id": sid,
            "role": row[0],
            "ts": row[2] or "",
            "content": text[:_GET_CHAR_CAP],
            "truncated": truncated,
        }, ensure_ascii=False)
    if kind == "part":
        pieces = rest.split(":")
        if len(pieces) != 3:
            return json.dumps({"ok": False, "error": f"malformed ref: {ref!r}"}, ensure_ascii=False)
        try:
            line_no, msg_idx = int(pieces[1]), int(pieces[2])
        except ValueError:
            return json.dumps({"ok": False, "error": f"malformed ref: {ref!r}"}, ensure_ascii=False)
        sid = pieces[0]
        hit = parts_index.fetch(sid, line_no, msg_idx)
        if hit is None:
            # The archive file may have been swept by retention — say so.
            return json.dumps({
                "ok": False,
                "error": f"archived message {ref!r} not found (the archive file may have been cleaned up)",
            }, ensure_ascii=False)
        msg = hit["message"]
        text = _content_text(msg.get("content") if isinstance(msg, dict) else msg)
        truncated = len(text) > _GET_CHAR_CAP
        return json.dumps({
            "ok": True,
            "ref": ref,
            "session_id": sid,
            "role": str(msg.get("role") or "") if isinstance(msg, dict) else "",
            "ts": hit["ts"],
            "archived": True,
            "content": text[:_GET_CHAR_CAP],
            "truncated": truncated,
        }, ensure_ascii=False)
    return json.dumps(
        {"ok": False, "error": f"unknown ref kind {kind!r} (expected live: or part:)"},
        ensure_ascii=False,
    )


# ── recent ───────────────────────────────────────────────────────────────────


def _fmt_role_content(m: Any) -> tuple[str, str]:
    role = getattr(m, "role", None)
    if isinstance(m, dict):
        role = m.get("role")
    role = str(getattr(role, "value", role) or "")
    return role, _content_text(getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else None))


def _handle_recent(args: dict[str, Any]) -> str:
    try:
        n = max(1, min(int(args.get("n") or 10), 30))
    except (TypeError, ValueError):
        n = 10
    history = CURRENT_HISTORY.get([])
    if not history:
        return json.dumps({"ok": True, "messages": [], "hint": "No history in this session yet."}, ensure_ascii=False)
    out = []
    for m in history[-n:]:
        role, text = _fmt_role_content(m)
        if len(text) > _RECENT_MSG_CAP:
            text = text[:_RECENT_MSG_CAP] + f" … [truncated, {len(text)} chars total]"
        out.append({"role": role, "content": text})
    return json.dumps({
        "ok": True,
        "count": len(out),
        "messages": out,
        "hint": "These are the verbatim last messages of this session (the working window's tail).",
    }, ensure_ascii=False)


# ── dispatch ─────────────────────────────────────────────────────────────────


def handle_history_recall(args: dict[str, Any]) -> str:
    if not _enabled():
        return json.dumps(
            {"ok": False, "error": "history_recall is disabled ([agent].history_recall = false)"},
            ensure_ascii=False,
        )
    action = str(args.get("action") or "").strip()
    if action == "search":
        return _handle_search(args)
    if action == "get":
        return _handle_get(args)
    if action == "recent":
        return _handle_recent(args)
    return json.dumps(
        {"ok": False, "error": f"unknown action {action!r} (use search / get / recent)"},
        ensure_ascii=False,
    )
