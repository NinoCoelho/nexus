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

from ...i18n import t
from ..deps import get_agent, get_locale, get_sessions
from ..schemas import CompactRequest, TruncateRequest
from ..session_store import SessionStore

log = logging.getLogger(__name__)

router = APIRouter()

_PARTIAL_PREFIXES = (
    "[context_overflow]",
    "[message_too_large]",
    "[empty_response]",
    "[llm_error]",
    "[crashed]",
    "[upstream_timeout]",
    "[interrupted]",
    "[cancelled]",
    "[rate_limited]",
    "[iteration_limit]",
    "[budget_exceeded]",
)


def _clear_partial_prefixes(history: list[Any]) -> list[Any]:
    from ...agent.llm import ChatMessage, Role

    out: list[Any] = []
    for msg in history:
        if (
            isinstance(msg, ChatMessage)
            and msg.role == Role.ASSISTANT
            and msg.content
        ):
            stripped = msg.content.strip()
            for prefix in _PARTIAL_PREFIXES:
                if stripped == prefix or stripped.startswith(prefix + " "):
                    new_content = stripped[len(prefix):].strip()
                    out.append(
                        ChatMessage(
                            role=msg.role,
                            content=new_content,
                            tool_calls=msg.tool_calls,
                            tool_call_id=msg.tool_call_id,
                            name=msg.name,
                        )
                    )
                    break
            else:
                out.append(msg)
        else:
            out.append(msg)
    return out


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
    locale: str = Depends(get_locale),
) -> dict:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("errors.sessions.not_found_named", locale, session_id=session_id),
        )
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
    locale: str = Depends(get_locale),
) -> None:
    """Set or clear the pinned flag for a message. Body: ``{"pinned": bool}``."""
    if store.get(session_id) is None:
        raise HTTPException(status_code=404, detail=t("errors.sessions.not_found", locale))
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
    locale: str = Depends(get_locale),
) -> dict:
    """Return Atropos trajectory records for this session.

    ``enabled`` reflects whether the trajectory logger is wired up
    (controlled by ``NEXUS_TRAJECTORIES=1``). When disabled, ``records``
    is always empty — the UI uses this to decide whether to surface
    the "view trajectory" affordance.
    """
    from .chat import _trajectory_logger

    if store.get(session_id) is None:
        raise HTTPException(status_code=404, detail=t("errors.sessions.not_found", locale))
    if _trajectory_logger is None:
        return {"enabled": False, "records": []}
    records = _trajectory_logger.find_for_session(session_id, limit=max(1, min(limit, 200)))
    return {"enabled": True, "records": records}


_KNOWN_WINDOWS: dict[str, int] = {
    "gemini-2.5-flash": 1_048_576,
    "gemini-2.5-pro": 1_048_576,
    "gemini-2.0-flash": 1_048_576,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4.1": 1_047_576,
    "gpt-4.1-mini": 1_047_576,
    "gpt-4.1-nano": 1_047_576,
    "o3": 200_000,
    "o4-mini": 200_000,
    "claude-sonnet-4-20250514": 200_000,
    "claude-3.5-sonnet": 200_000,
    "claude-3.7-sonnet": 200_000,
    "glm-4.7": 128_000,
    "glm-5": 128_000,
    "glm-5.1": 128_000,
    "deepseek-r1": 128_000,
    "deepseek-chat": 128_000,
}


def _known_context_window(model: str) -> int:
    if not model:
        return 0
    name = model.split("/")[-1]
    return _KNOWN_WINDOWS.get(name, 0)


@router.get("/sessions/{session_id}/usage")
async def get_session_usage(
    session_id: str,
    store: SessionStore = Depends(get_sessions),
    locale: str = Depends(get_locale),
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
        raise HTTPException(status_code=404, detail=t("errors.sessions.not_found", locale))

    from ...usage_pricing import estimate_cost

    model = row[0] or ""
    in_tok = int(row[1] or 0)
    out_tok = int(row[2] or 0)
    tool_calls = int(row[3] or 0)
    cost, cost_status = (None, "unknown")
    if model:
        cost, cost_status = estimate_cost(model, input_tokens=in_tok, output_tokens=out_tok)

    context_window_tokens = 0
    estimated_context_tokens = 0
    context_pct = 0.0
    context_zone = "unknown"
    try:
        from ...config_file import load as load_config
        from ...agent.loop.overflow import estimate_tokens
        from ...agent.loop.zones import classify_zone

        _FALLBACK_WINDOW = 128_000
        cfg = load_config()
        for entry in cfg.models:
            if entry.id == model or entry.model_name == model:
                context_window_tokens = int(entry.context_window or 0)
                break
        if context_window_tokens == 0:
            context_window_tokens = _known_context_window(model)
        session_obj = store.get(session_id)
        if session_obj and session_obj.history:
            estimated_context_tokens = estimate_tokens(session_obj.history)
            effective_window = context_window_tokens if context_window_tokens > 0 else _FALLBACK_WINDOW
            context_pct = round(
                min(estimated_context_tokens / effective_window, 1.0), 4
            )
            if context_window_tokens > 0:
                context_zone = classify_zone(estimated_context_tokens, context_window_tokens)
            elif context_pct > 0.8:
                context_zone = "red"
            elif context_pct > 0.6:
                context_zone = "orange"
    except Exception:
        log.debug("context-zone enrichment failed", exc_info=True)

    return {
        "model": model or None,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "tool_call_count": tool_calls,
        "estimated_cost_usd": cost,
        "cost_status": cost_status,
        "context_window_tokens": context_window_tokens,
        "estimated_context_tokens": estimated_context_tokens,
        "context_pct": context_pct,
        "context_zone": context_zone,
    }


@router.patch("/sessions/{session_id}/messages/{seq}/feedback", status_code=status.HTTP_204_NO_CONTENT)
async def set_message_feedback(
    session_id: str,
    seq: int,
    body: dict,
    store: SessionStore = Depends(get_sessions),
    locale: str = Depends(get_locale),
) -> None:
    """Set or clear thumbs feedback for a single assistant message.

    Body: ``{"value": "up" | "down" | null}``. ``null`` clears the entry.
    """
    if store.get(session_id) is None:
        raise HTTPException(status_code=404, detail=t("errors.sessions.not_found", locale))
    raw_value = body.get("value")
    if raw_value is not None and raw_value not in ("up", "down"):
        raise HTTPException(status_code=422, detail=t("errors.sessions.bad_feedback", locale))
    try:
        store.set_feedback(session_id, seq, raw_value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.patch("/sessions/{session_id}/truncate", status_code=status.HTTP_204_NO_CONTENT)
async def truncate_session(
    session_id: str,
    body: TruncateRequest,
    store: SessionStore = Depends(get_sessions),
    locale: str = Depends(get_locale),
) -> None:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=t("errors.sessions.not_found", locale))
    truncated = session.history[: body.before_seq]
    store.replace_history(session_id, truncated)


@router.delete("/sessions/{session_id}/messages/last")
async def rollback_last_message(
    session_id: str,
    store: SessionStore = Depends(get_sessions),
    locale: str = Depends(get_locale),
) -> dict:
    """Remove the last user message (and any trailing assistant) from history.

    Recovery path for sessions stuck due to oversized messages that triggered
    ``context_overflow`` or ``message_too_large`` — without this, the session
    is permanently broken because every turn immediately fails the pre-flight
    overflow check.
    """
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=t("errors.sessions.not_found", locale))
    history = list(session.history)
    if not history:
        raise HTTPException(status_code=422, detail="Session history is empty")
    removed = 0
    while history and history[-1].role.value != "user":
        history.pop()
        removed += 1
    user_content: str | None = None
    if history:
        last_user = history.pop()
        removed += 1
        if isinstance(last_user.content, str):
            user_content = last_user.content
        elif isinstance(last_user.content, list):
            parts = [p.text for p in last_user.content if getattr(p, "kind", None) == "text" and p.text]
            user_content = " ".join(parts) if parts else None
    store.replace_history(session_id, history)
    return {"removed_count": removed, "remaining_messages": len(history), "removed_user_content": user_content}


@router.post("/sessions/{session_id}/compact")
async def compact_session(
    session_id: str,
    request: Request,
    body: CompactRequest | None = None,
    store: SessionStore = Depends(get_sessions),
    locale: str = Depends(get_locale),
) -> dict:
    """Compact oversized tool results and summarize older turns.

    Runs the full pipeline: auto-compact (tool result compression) followed
    by LLM summarization of older turns when the session is still in
    orange/red zone. Also clears partial-turn banners (``[context_overflow]``
    etc.) so the UI doesn't re-show them after reload.
    """
    from ...agent.loop.compact import compact_and_summarize
    from ...agent.loop import Agent

    agent: Agent = get_agent(request)
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=t("errors.sessions.not_found", locale))

    model_id = body.model if body and body.model else None
    if not model_id:
        cfg = getattr(request.app.state, "mutable_state", {}).get("cfg")
        model_id = getattr(getattr(cfg, "agent", None), "default_model", None) or None
    provider, upstream_model = agent._resolve_provider(model_id)
    context_window = agent._context_window_for(upstream_model or model_id)
    if context_window == 0 and (upstream_model or model_id) in _KNOWN_WINDOWS:
        context_window = _KNOWN_WINDOWS[upstream_model or model_id]

    new_history, report = await compact_and_summarize(
        session.history,
        context_window=context_window,
        session_id=session_id,
        model_id=upstream_model or model_id,
        provider=provider,
    )

    log.info(
        "compact session=%s: model=%s ctx_window=%d "
        "compacted=%d summarized=%s tokens=%d->%d zone=%s still_overflowed=%s",
        session_id, upstream_model or model_id, context_window,
        report.compact_report.compacted, report.summarized,
        report.tokens_before, report.tokens_after,
        report.zone_after, report.still_overflowed,
    )

    if report.compact_report.compacted > 0 or report.summarized:
        new_history = _clear_partial_prefixes(new_history)
        store.replace_history(session_id, new_history)

    return {
        "inspected_tool_messages": report.compact_report.inspected,
        "compacted": report.compact_report.compacted,
        "skipped_already_compacted": report.compact_report.skipped_already_compacted,
        "bytes_before": report.compact_report.bytes_before,
        "bytes_after": report.compact_report.bytes_after,
        "saved_bytes": report.compact_report.saved_bytes,
        "summarized": report.summarized,
        "summarized_messages": report.summarized_messages,
        "messages_before": report.messages_before,
        "messages_after": report.messages_after,
        "tokens_before": report.tokens_before,
        "tokens_after": report.tokens_after,
        "zone_after": report.zone_after,
        "still_overflowed": report.still_overflowed,
        "budget_exceeded": report.budget_exceeded,
    }


@router.get("/sessions/{session_id}/export")
async def export_session(
    session_id: str,
    store: SessionStore = Depends(get_sessions),
    locale: str = Depends(get_locale),
) -> StreamingResponse:
    from datetime import datetime, timezone
    import re

    session = store.get(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("errors.sessions.not_found_named", locale, session_id=session_id),
        )

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


@router.get("/sessions/{session_id}/paused")
async def get_paused_turn(
    session_id: str,
    store: SessionStore = Depends(get_sessions),
) -> dict:
    paused = store.load_paused(session_id)
    if not paused:
        return {"ok": True, "paused": False}
    from datetime import datetime, timezone
    try:
        retry_after = datetime.fromisoformat(paused["retry_after"])
        now = datetime.now(timezone.utc)
        remaining = max(0, int((retry_after - now).total_seconds()))
    except (ValueError, TypeError):
        remaining = 0
    return {
        "ok": True,
        "paused": True,
        "retry_after": paused["retry_after"],
        "remaining_seconds": remaining,
        "error_detail": paused.get("error_detail"),
        "model_id": paused.get("model_id"),
        "resume_count": paused.get("resume_count", 0),
    }


@router.post("/sessions/{session_id}/resume-paused")
async def resume_paused_turn(
    session_id: str,
    store: SessionStore = Depends(get_sessions),
) -> dict:
    paused = store.resume_paused(session_id)
    if not paused:
        raise HTTPException(status_code=404, detail="No paused turn for this session")
    from datetime import datetime, timezone
    try:
        retry_after = datetime.fromisoformat(paused["retry_after"])
        now = datetime.now(timezone.utc)
        remaining = (retry_after - now).total_seconds()
    except (ValueError, TypeError):
        remaining = 0
    if remaining > 0:
        raise HTTPException(
            status_code=425,
            detail=f"Cooldown not elapsed. Wait {int(remaining)} more seconds.",
            headers={"Retry-After": str(int(remaining))},
        )
    return {
        "ok": True,
        "working_messages_json": paused["working_messages_json"],
        "user_message": paused["user_message"],
        "model_id": paused.get("model_id"),
    }
