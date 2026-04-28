"""Routes for session management: list/search/get/rename/delete/truncate/export.

Vault-save and import endpoints live in sessions_vault.py to keep this file
under 300 lines.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from ..deps import get_sessions
from ..schemas import CompactRequest, TruncateRequest
from ..session_store import SessionStore

log = logging.getLogger(__name__)

router = APIRouter()


def _session_markdown(session: Any, sessions: SessionStore, include_frontmatter: bool = True) -> str:
    """Render a session as markdown. Shared between export and to-vault."""
    from datetime import datetime, timezone
    created_at_ts, updated_at_ts = sessions.get_session_timestamps(session.id)

    def _iso(ts: int) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    lines: list[str] = []
    if include_frontmatter:
        context_yaml = "null" if session.context is None else json.dumps(session.context)
        lines += [
            "---",
            f"nexus_session_id: {session.id}",
            f"title: {json.dumps(session.title)}",
            f"created_at: {_iso(created_at_ts)}",
            f"updated_at: {_iso(updated_at_ts)}",
            f"context: {context_yaml}",
            "---",
            "",
        ]

    ts_list: list[int] = getattr(session, "_message_timestamps", []) or []
    for i, msg in enumerate(session.history):
        role = str(msg.role.value if hasattr(msg.role, "value") else msg.role)
        if role not in ("user", "assistant"):
            continue
        content = (msg.content or "").strip()
        if not content:
            continue
        msg_ts = ts_list[i] if i < len(ts_list) else created_at_ts
        label = "You" if role == "user" else "Nexus"
        lines.append(f"## {label} · {_iso(msg_ts)}")
        lines.append("")
        lines.append(content)
        lines.append("")
    return "\n".join(lines)


@router.get("/sessions")
async def list_sessions(
    limit: int = 50,
    include_hidden: bool = False,
    store: SessionStore = Depends(get_sessions),
) -> list[dict]:
    summaries = store.list(limit=limit, include_hidden=include_hidden)
    return [
        {
            "id": s.id,
            "title": s.title,
            "created_at": s.created_at,
            "updated_at": s.updated_at,
            "message_count": s.message_count,
        }
        for s in summaries
    ]


@router.get("/sessions/search")
async def search_sessions(
    q: str = "",
    limit: int = 20,
    store: SessionStore = Depends(get_sessions),
) -> list[dict]:
    """Full-text search over session message content.

    Returns ``[]`` for a blank ``q``. Results are ordered by BM25
    relevance and include ``session_id``, ``title``, and a ``snippet``
    with matching terms wrapped in ``**``.
    """
    if not q.strip():
        return []
    return store.search(q.strip(), limit=max(1, min(limit, 100)))


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    store: SessionStore = Depends(get_sessions),
) -> dict:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"session {session_id!r} not found")
    ts_list = getattr(session, "_message_timestamps", []) or []
    from datetime import datetime, timezone
    def _iso(ts: int | None) -> str | None:
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    feedback_map = store.get_feedback_map(session_id)
    pinned_set = store.get_pinned_set(session_id)
    return {
        "id": session.id,
        "title": session.title,
        "context": session.context,
        "messages": [
            {
                "seq": i,
                "role": m.role,
                "content": m.content,
                "tool_calls": [tc.model_dump() for tc in m.tool_calls] if m.tool_calls else None,
                "tool_call_id": m.tool_call_id,
                "created_at": _iso(ts_list[i] if i < len(ts_list) else None),
                "feedback": feedback_map.get(i),
                "pinned": i in pinned_set,
            }
            for i, m in enumerate(session.history)
        ],
    }


@router.patch("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def rename_session(
    session_id: str,
    body: dict,
    store: SessionStore = Depends(get_sessions),
) -> None:
    title = body.get("title")
    if title is not None:
        store.rename(session_id, title)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    store: SessionStore = Depends(get_sessions),
) -> None:
    store.delete(session_id)


@router.patch("/sessions/{session_id}/messages/{seq}/pin", status_code=status.HTTP_204_NO_CONTENT)
async def set_message_pin(
    session_id: str,
    seq: int,
    body: dict,
    store: SessionStore = Depends(get_sessions),
) -> None:
    """Set or clear the pinned flag for a message. Body: ``{"pinned": bool}``."""
    if store.get(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    pinned = bool(body.get("pinned", False))
    store.set_pinned(session_id, seq, pinned)


@router.get("/pins")
async def list_pins(
    limit: int = 50,
    store: SessionStore = Depends(get_sessions),
) -> list[dict]:
    """Return pinned messages across all sessions, newest first."""
    return store.list_pinned_across_sessions(limit=limit)


@router.get("/sessions/{session_id}/trajectories")
async def get_session_trajectories(
    session_id: str,
    limit: int = 50,
    store: SessionStore = Depends(get_sessions),
) -> dict:
    """Return Atropos trajectory records for this session.

    ``enabled`` reflects whether the trajectory logger is wired up
    (controlled by ``NEXUS_TRAJECTORIES=1``). When disabled, ``records``
    is always empty — the UI uses this to decide whether to surface
    the "view trajectory" affordance.
    """
    from .chat import _trajectory_logger

    if store.get(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if _trajectory_logger is None:
        return {"enabled": False, "records": []}
    records = _trajectory_logger.find_for_session(session_id, limit=max(1, min(limit, 200)))
    return {"enabled": True, "records": records}


@router.get("/sessions/{session_id}/usage")
async def get_session_usage(
    session_id: str,
    store: SessionStore = Depends(get_sessions),
) -> dict:
    """Return token/tool/cost totals for a single session.

    Powers the live "agent status bar" in the UI. Pricing is best-effort:
    ``estimated_cost_usd`` is ``null`` when the model has no entry in the
    pricing table.
    """
    row = store._loom._db.execute(
        "SELECT model, input_tokens, output_tokens, tool_call_count "
        "FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")

    from ...usage_pricing import estimate_cost

    model = row[0] or ""
    in_tok = int(row[1] or 0)
    out_tok = int(row[2] or 0)
    tool_calls = int(row[3] or 0)
    cost, cost_status = (None, "unknown")
    if model:
        cost, cost_status = estimate_cost(model, input_tokens=in_tok, output_tokens=out_tok)
    return {
        "model": model or None,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "tool_call_count": tool_calls,
        "estimated_cost_usd": cost,
        "cost_status": cost_status,
    }


@router.patch("/sessions/{session_id}/messages/{seq}/feedback", status_code=status.HTTP_204_NO_CONTENT)
async def set_message_feedback(
    session_id: str,
    seq: int,
    body: dict,
    store: SessionStore = Depends(get_sessions),
) -> None:
    """Set or clear thumbs feedback for a single assistant message.

    Body: ``{"value": "up" | "down" | null}``. ``null`` clears the entry.
    """
    if store.get(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    raw_value = body.get("value")
    if raw_value is not None and raw_value not in ("up", "down"):
        raise HTTPException(status_code=422, detail="value must be 'up', 'down', or null")
    try:
        store.set_feedback(session_id, seq, raw_value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.patch("/sessions/{session_id}/truncate", status_code=status.HTTP_204_NO_CONTENT)
async def truncate_session(
    session_id: str,
    body: TruncateRequest,
    store: SessionStore = Depends(get_sessions),
) -> None:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    truncated = session.history[: body.before_seq]
    store.replace_history(session_id, truncated)


@router.post("/sessions/{session_id}/compact")
async def compact_session(
    session_id: str,
    body: CompactRequest | None = None,
    store: SessionStore = Depends(get_sessions),
) -> dict:
    """Compact oversized tool results in a session's history.

    Triggered by the UI's "Compact history" button when a turn fails with
    ``context_overflow`` (or proactively from the chat menu). Returns a
    report so the UI can show "saved 1.2 MB across 3 messages".
    """
    from ...agent.loop.compact import compact_history

    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    body = body or CompactRequest()
    new_history, report = compact_history(
        session.history,
        threshold_bytes=body.threshold_bytes,
        head_keep=body.head_keep_bytes,
        sample_rows=body.csv_sample_rows,
    )
    if report.compacted > 0:
        store.replace_history(session_id, new_history)
    return {
        "inspected_tool_messages": report.inspected,
        "compacted": report.compacted,
        "skipped_already_compacted": report.skipped_already_compacted,
        "bytes_before": report.bytes_before,
        "bytes_after": report.bytes_after,
        "saved_bytes": report.saved_bytes,
    }


@router.get("/sessions/{session_id}/export")
async def export_session(
    session_id: str,
    store: SessionStore = Depends(get_sessions),
) -> StreamingResponse:
    from datetime import datetime, timezone
    import re

    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"session {session_id!r} not found")

    # Gather session-level timestamps from the store.
    created_at_ts, updated_at_ts = store.get_session_timestamps(session_id)

    def _iso(ts: int) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    # Build frontmatter (hand-rolled, no nested objects).
    context_val = session.context
    if context_val is None:
        context_yaml = "null"
    else:
        context_yaml = json.dumps(context_val)

    title_yaml = json.dumps(session.title)
    lines: list[str] = [
        "---",
        f"nexus_session_id: {session.id}",
        f"title: {title_yaml}",
        f"created_at: {_iso(created_at_ts)}",
        f"updated_at: {_iso(updated_at_ts)}",
        f"context: {context_yaml}",
        "---",
        "",
    ]

    ts_list: list[int] = getattr(session, "_message_timestamps", []) or []

    for i, msg in enumerate(session.history):
        role = str(msg.role.value if hasattr(msg.role, "value") else msg.role)
        # Skip tool/system messages and empty content.
        if role not in ("user", "assistant"):
            continue
        content = (msg.content or "").strip()
        if not content:
            continue
        msg_ts = ts_list[i] if i < len(ts_list) else created_at_ts
        label = "You" if role == "user" else "Nexus"
        lines.append(f"## {label} · {_iso(msg_ts)}")
        lines.append("")
        lines.append(content)
        lines.append("")

    markdown = "\n".join(lines)

    # Build a safe filename slug from the title.
    slug = re.sub(r"[^a-z0-9]+", "-", session.title.lower()).strip("-")[:40]
    id8 = session.id[:8]
    filename = f"session-{slug}-{id8}.md"

    return StreamingResponse(
        iter([markdown]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
