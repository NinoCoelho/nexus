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
