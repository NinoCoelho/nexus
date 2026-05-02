"""Routes for vault data-dashboard operations: /vault/dashboard*."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from ...agent.loop import Agent
from ..deps import get_agent, get_sessions
from ..session_store import SessionStore

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/vault/dashboard")
async def vault_dashboard_get(folder: str = "") -> dict:
    """Return the dashboard for ``folder``. Lazy: missing `_data.md` returns
    sensible defaults with ``exists: false`` and does NOT touch disk."""
    from ... import vault_dashboard
    return vault_dashboard.read_dashboard(folder)


@router.put("/vault/dashboard")
async def vault_dashboard_put(body: dict) -> dict:
    """Patch the dashboard. Materializes `_data.md` if absent."""
    from ... import vault_dashboard
    folder = body.get("folder", "")
    if not isinstance(folder, str):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`folder` must be a string")
    patch = {k: body[k] for k in ("title", "chat_session_id", "operations") if k in body}
    try:
        return vault_dashboard.patch_dashboard(folder, patch)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/vault/dashboard/operations", status_code=status.HTTP_201_CREATED)
async def vault_dashboard_add_operation(body: dict) -> dict:
    """Append or replace an operation (by id) on the dashboard."""
    from ... import vault_dashboard
    folder = body.get("folder", "")
    op = body.get("operation")
    if not isinstance(op, dict):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`operation` must be an object")
    try:
        return vault_dashboard.upsert_operation(folder, op)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.delete("/vault/dashboard/operations/{op_id}", status_code=status.HTTP_200_OK)
async def vault_dashboard_delete_operation(op_id: str, folder: str = "") -> dict:
    from ... import vault_dashboard
    return vault_dashboard.delete_operation(folder, op_id)


def _context_prefix_for(folder: str) -> str:
    """Stable prefix used to tag dashboard-op run sessions in their context.

    Matches the format produced by ``vault_dashboard_run_operation`` below.
    Centralised so the run-history query can't drift from the writer.
    """
    return f"Dashboard op: {folder}#"


def _derive_run_status(session: object) -> tuple[str, str | None]:
    """Inspect a persisted session's last assistant message to derive run status.

    The background-turn helper persists ``[interrupted]`` / ``[crashed]`` /
    ``[llm_error]`` / ``[background_interrupted]`` / ``[empty_response]`` etc.
    as a leading bracketed marker on partial runs. Anything else is a
    successful turn. Returns ``("done", None)`` or ``("failed", <preview>)``.
    """
    history = list(getattr(session, "history", []) or [])
    last = history[-1] if history else None
    content = (getattr(last, "content", "") or "") if last is not None else ""
    if last is None or content.startswith("["):
        return "failed", (content[:200] or "Action did not complete.")
    return "done", None


@router.get("/vault/dashboard/run-history")
async def vault_dashboard_run_history(
    folder: str = "",
    store: SessionStore = Depends(get_sessions),
) -> dict:
    """Return the most recent run per dashboard operation for ``folder``.

    Used by the UI to rehydrate the per-chip last-run state after a reload
    or view switch — failures persist (warning icon stays until clicked),
    successes get GC'd by the UI once acknowledged or shown.

    Response: ``{folder, runs: [{op_id, session_id, status, error, at}, ...]}``.
    Sorted newest-first per op; only the latest run for each op is returned.
    """
    prefix = _context_prefix_for(folder)
    rows = store.list_hidden_by_context_prefix(prefix)
    seen: set[str] = set()
    runs: list[dict] = []
    for row in rows:
        ctx = row.get("context") or ""
        op_id = ctx[len(prefix):].strip() if ctx.startswith(prefix) else ""
        if not op_id or op_id in seen:
            continue
        seen.add(op_id)
        sess = store.get(row["id"])
        if sess is None:
            continue
        outcome, err = _derive_run_status(sess)
        runs.append({
            "op_id": op_id,
            "session_id": row["id"],
            "status": outcome,
            "error": err,
            "at": row.get("updated_at"),
        })
    return {"folder": folder, "runs": runs}


@router.post("/vault/dashboard/run-operation", status_code=status.HTTP_201_CREATED)
async def vault_dashboard_run_operation(
    body: dict,
    a: Agent = Depends(get_agent),
    store: SessionStore = Depends(get_sessions),
) -> dict:
    """Run a chat-kind dashboard operation in an ephemeral hidden session.

    Body: ``{folder, op_id}``.

    Loads the operation, creates a session marked hidden (so it never
    appears in the sidebar), kicks the agent loop in the background, and
    returns ``{session_id}``. The UI subscribes to
    ``/chat/{session_id}/events`` to track progress and reuses
    ``CardActivityModal`` to render the result on demand.

    Form-kind operations don't go through here — the UI opens the form
    inline against the target table directly.
    """
    from ... import vault_dashboard
    from ..events import SessionEvent
    from .vault_dispatch import HIDDEN_SEED_MARKER, _resolve_dispatch_model
    from .vault_dispatch_helpers import run_background_agent_turn

    folder = body.get("folder")
    op_id = body.get("op_id") or body.get("operation_id")
    if not isinstance(folder, str) or not isinstance(op_id, str) or not op_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`folder` and `op_id` required",
        )

    dashboard = vault_dashboard.read_dashboard(folder)
    operation = next(
        (op for op in dashboard.get("operations") or [] if op.get("id") == op_id),
        None,
    )
    if operation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"operation {op_id!r} not found in dashboard",
        )
    if operation.get("kind") != "chat":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="run-operation only supports kind='chat' operations",
        )

    prompt = (operation.get("prompt") or "").strip() or operation.get("label") or ""
    label = operation.get("label") or op_id
    title = (dashboard.get("title") or folder or "(root)").strip()

    # Seed message embeds the hidden marker so any UI that reuses the chat
    # view filters it out of the displayed list (CardActivityModal does the
    # same). The agent still sees the full content as a normal user message.
    seed_lines = [
        HIDDEN_SEED_MARKER.rstrip(),
        f"You are running quick action **{label}** on database **{title}** "
        f"(vault folder `{folder or '/'}`).",
        "",
        "How this runs:",
        "- There is no chat UI for this turn. The user kicked this from a "
        "dashboard chip and will see your **final assistant message** in a "
        "preview modal.",
        "- If you need any input from the user (a missing value, an "
        "ambiguous choice, a confirmation before a write), call the "
        "`ask_user` tool. The user will see a popup; do not guess.",
        "- Keep your final reply terse and result-shaped. No preamble like "
        "\"Here's what I did\". No closing remarks. Just the artifact: a "
        "short status line, the table you produced, the chart fence, etc.",
        "",
        prompt,
    ]
    seed_message = "\n".join(seed_lines)

    session = store.create(context=f"{_context_prefix_for(folder)}{op_id}")
    try:
        store.mark_hidden(session.id)
    except Exception:
        log.exception("run-operation: mark_hidden failed")
    try:
        store.rename(session.id, f"⚡ {label}"[:60])
    except Exception:
        log.exception("run-operation: title rename failed")

    resolved_model = _resolve_dispatch_model(None, a)

    async def _run_with_terminal_event() -> None:
        """Wrap the background turn so the chip can detect success/failure.

        ``run_background_agent_turn`` swallows its own exceptions and persists
        a partial history on crash. To give the chip a single, reliable
        terminal signal, we inspect the persisted history after the turn
        and publish a synthetic ``op_done`` event over the session bus.
        """
        outcome = "done"
        error_msg: str | None = None
        try:
            await run_background_agent_turn(
                session_id=session.id,
                seed_message=seed_message,
                card_path="",
                card_id="",
                agent_=a,
                store=store,
                model_id=resolved_model,
                entity_kind="none",
            )
        except Exception as exc:
            outcome = "failed"
            error_msg = str(exc)
        else:
            try:
                final = store.get_or_create(session.id)
                history = list(final.history)
                last = history[-1] if history else None
                content = (getattr(last, "content", "") or "") if last is not None else ""
                # Helper persists ``[background_interrupted]`` / ``[crashed]``
                # / ``[llm_error]`` etc. on partial runs — treat any of those
                # as a failure so the chip flags it for the user.
                if last is None or content.startswith("["):
                    outcome = "failed"
                    error_msg = content[:200] or "Action did not complete."
            except Exception:
                log.exception("run-operation: outcome inspection failed")
        try:
            store.publish(
                session.id,
                SessionEvent(
                    kind="op_done",
                    data={"status": outcome, "error": error_msg},
                ),
            )
        except Exception:
            log.exception("run-operation: terminal event publish failed")

    asyncio.create_task(_run_with_terminal_event())

    return {
        "session_id": session.id,
        "folder": folder,
        "op_id": op_id,
        "status": "running",
    }


_WIDGET_KIND_INSTRUCTIONS: dict[str, str] = {
    "chart": (
        "OUTPUT FORMAT: Reply with **exactly one** ```nexus-chart fenced "
        "block`` and nothing else. No prose before or after, no headings, "
        "no analysis. Use the `visualize_table` tool against a vault data-"
        "table when possible — its return value is already the right shape."
    ),
    "report": (
        "OUTPUT FORMAT: Reply with terse markdown (bullets, short "
        "paragraphs, or a small table). No preamble (\"Here's the "
        "report\"), no closing remarks, no analysis unless the user's "
        "prompt explicitly asked for analysis."
    ),
    "kpi": (
        "OUTPUT FORMAT: Reply with **one number** on the first line and "
        "**one short label** on the second line. Nothing else. Example:\n"
        "    1,247\n"
        "    Open issues"
    ),
}


@router.post("/vault/dashboard/widgets", status_code=status.HTTP_201_CREATED)
async def vault_dashboard_add_widget(body: dict) -> dict:
    """Append or replace a widget on the dashboard."""
    from ... import vault_dashboard
    folder = body.get("folder", "")
    widget = body.get("widget")
    if not isinstance(widget, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`widget` must be an object",
        )
    try:
        return vault_dashboard.upsert_widget(folder, widget)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc),
        )


@router.delete("/vault/dashboard/widgets/{widget_id}")
async def vault_dashboard_delete_widget(widget_id: str, folder: str = "") -> dict:
    from ... import vault_dashboard
    return vault_dashboard.delete_widget(folder, widget_id)


@router.get("/vault/dashboard/widgets/{widget_id}/content")
async def vault_dashboard_widget_content(widget_id: str, folder: str = "") -> dict:
    """Return the widget's current result body. Empty string if not refreshed yet."""
    from ... import vault_widgets
    try:
        body = vault_widgets.read_widget_result(folder, widget_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc),
        )
    return {"folder": folder, "widget_id": widget_id, "content": body}


def _widget_context_prefix(folder: str) -> str:
    return f"Widget refresh: {folder}#"


@router.post(
    "/vault/dashboard/widgets/{widget_id}/refresh",
    status_code=status.HTTP_201_CREATED,
)
async def vault_dashboard_refresh_widget(
    widget_id: str,
    body: dict,
    a: Agent = Depends(get_agent),
    store: SessionStore = Depends(get_sessions),
) -> dict:
    """Run a hidden refresh turn for a widget.

    Body: ``{folder}``. Looks up the widget config in `_data.md`, kicks an
    ephemeral hidden agent session with a kind-specific output-format seed,
    and on terminal: writes the agent's last assistant message verbatim into
    ``<folder>/_widgets/<widget_id>.md`` and stamps ``last_refreshed_at``.

    Returns ``{session_id}`` so the UI can subscribe to ``op_done`` and pull
    fresh content.
    """
    from datetime import datetime, timezone
    from ... import vault_dashboard, vault_widgets
    from ..events import SessionEvent
    from .vault_dispatch import HIDDEN_SEED_MARKER, _resolve_dispatch_model
    from .vault_dispatch_helpers import run_background_agent_turn

    folder = body.get("folder")
    if not isinstance(folder, str):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`folder` required",
        )

    dashboard = vault_dashboard.read_dashboard(folder)
    widget = next(
        (w for w in dashboard.get("widgets") or [] if w.get("id") == widget_id),
        None,
    )
    if widget is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"widget {widget_id!r} not found in dashboard",
        )

    kind = widget.get("kind", "report")
    title = widget.get("title") or widget_id
    user_prompt = (widget.get("prompt") or "").strip() or title
    db_title = (dashboard.get("title") or folder or "(root)").strip()
    output_rule = _WIDGET_KIND_INSTRUCTIONS.get(
        kind, _WIDGET_KIND_INSTRUCTIONS["report"],
    )

    seed_lines = [
        HIDDEN_SEED_MARKER.rstrip(),
        f"You are refreshing widget **{title}** ({kind}) on database "
        f"**{db_title}** (vault folder `{folder or '/'}`).",
        "",
        output_rule,
        "",
        "Use the available vault tools (read tables, search, visualize) to "
        "compute the answer. Do NOT call `ask_user` — widgets refresh "
        "without user interaction. If something is ambiguous, make a "
        "reasonable assumption and proceed.",
        "",
        "User's widget prompt:",
        user_prompt,
    ]
    seed_message = "\n".join(seed_lines)

    session = store.create(context=f"{_widget_context_prefix(folder)}{widget_id}")
    try:
        store.mark_hidden(session.id)
    except Exception:
        log.exception("widget refresh: mark_hidden failed")
    try:
        store.rename(session.id, f"🔄 {title}"[:60])
    except Exception:
        log.exception("widget refresh: title rename failed")

    resolved_model = _resolve_dispatch_model(None, a)

    async def _run_with_capture() -> None:
        outcome = "done"
        error_msg: str | None = None
        try:
            await run_background_agent_turn(
                session_id=session.id,
                seed_message=seed_message,
                card_path="",
                card_id="",
                agent_=a,
                store=store,
                model_id=resolved_model,
                entity_kind="none",
            )
        except Exception as exc:
            outcome = "failed"
            error_msg = str(exc)
        else:
            try:
                final = store.get_or_create(session.id)
                history = list(final.history)
                last = history[-1] if history else None
                content = (getattr(last, "content", "") or "") if last is not None else ""
                if last is None or content.startswith("["):
                    outcome = "failed"
                    error_msg = content[:200] or "Widget refresh did not complete."
                else:
                    # Capture the agent's terse final reply as the widget body.
                    try:
                        vault_widgets.write_widget_result(folder, widget_id, content)
                    except Exception as exc:
                        outcome = "failed"
                        error_msg = f"failed to persist widget result: {exc}"
                    else:
                        try:
                            now = datetime.now(timezone.utc).isoformat(
                                timespec="seconds",
                            ).replace("+00:00", "Z")
                            vault_dashboard.set_widget_refreshed(
                                folder, widget_id, now,
                            )
                        except Exception:
                            log.exception(
                                "widget refresh: timestamp stamp failed",
                            )
            except Exception:
                log.exception("widget refresh: outcome inspection failed")
        try:
            store.publish(
                session.id,
                SessionEvent(
                    kind="op_done",
                    data={"status": outcome, "error": error_msg},
                ),
            )
        except Exception:
            log.exception("widget refresh: terminal event publish failed")

    asyncio.create_task(_run_with_capture())

    return {
        "session_id": session.id,
        "folder": folder,
        "widget_id": widget_id,
        "status": "running",
    }


@router.delete("/vault/dashboard")
async def vault_dashboard_delete_database(folder: str, confirm: str) -> dict:
    """Delete an entire database (folder of data-tables + `_data.md`).

    ``confirm`` must equal the folder's basename — server-side guard against
    accidental wipes.
    """
    from ... import vault_dashboard
    try:
        return vault_dashboard.delete_database(folder, confirm=confirm)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
