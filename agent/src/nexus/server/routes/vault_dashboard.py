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


def _build_run_op_seed(
    *,
    folder: str,
    operation: dict,
    db_title: str,
    plan_only: bool,
    approved_plan: str | None,
) -> str:
    """Compose the hidden-session seed for a chat-kind operation.

    Three modes:
    * ``plan_only=True`` — agent produces a JSON plan, no writes.
    * ``approved_plan`` set — agent executes against the user-approved plan.
    * neither — direct execution (legacy path, no preview).
    """
    from .vault_dispatch import HIDDEN_SEED_MARKER

    prompt = (operation.get("prompt") or "").strip() or operation.get("label") or ""
    label = operation.get("label") or operation.get("id") or "operation"

    seed_lines: list[str] = [
        HIDDEN_SEED_MARKER.rstrip(),
        f"You are running quick action **{label}** on database **{db_title}** "
        f"(vault folder `{folder or '/'}`).",
        "",
    ]

    if plan_only:
        seed_lines.extend([_PLAN_INSTRUCTIONS, "", "User's instruction:", prompt])
    else:
        seed_lines.extend([
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
        ])
        if approved_plan:
            seed_lines.extend([
                "The user already reviewed and **approved** the following plan "
                "for this run. Execute it. Stay within the spirit of the plan; "
                "if you discover a step is no longer appropriate (e.g. the "
                "data already changed), explain and stop rather than freelance.",
                "",
                "```nexus-plan",
                approved_plan,
                "```",
                "",
            ])
        seed_lines.extend(["User's instruction:", prompt])

    return "\n".join(seed_lines)


async def _kick_chat_operation(
    *,
    folder: str,
    op_id: str,
    plan_only: bool,
    approved_plan: str | None,
    a: Agent,
    store: SessionStore,
) -> dict:
    """Shared body for direct-run, plan-only, and execute-after-approval."""
    from ... import vault_dashboard
    from ..events import SessionEvent
    from .vault_dispatch import _resolve_dispatch_model
    from .vault_dispatch_helpers import run_background_agent_turn

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

    label = operation.get("label") or op_id
    title = (dashboard.get("title") or folder or "(root)").strip()
    seed_message = _build_run_op_seed(
        folder=folder,
        operation=operation,
        db_title=title,
        plan_only=plan_only,
        approved_plan=approved_plan,
    )

    # Plan and execute use distinct context prefixes so run-history doesn't
    # mistake a planning session for a real run when rehydrating chip state.
    prefix = (
        f"Dashboard plan: {folder}#" if plan_only else _context_prefix_for(folder)
    )
    session = store.create(context=f"{prefix}{op_id}")
    try:
        store.mark_hidden(session.id)
    except Exception:
        log.exception("run-operation: mark_hidden failed")
    try:
        marker = "🧭" if plan_only else "⚡"
        store.rename(session.id, f"{marker} {label}"[:60])
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
    folder = body.get("folder")
    op_id = body.get("op_id") or body.get("operation_id")
    return await _kick_chat_operation(
        folder=folder,
        op_id=op_id,
        plan_only=False,
        approved_plan=None,
        a=a,
        store=store,
    )


@router.post(
    "/vault/dashboard/run-operation/plan",
    status_code=status.HTTP_201_CREATED,
)
async def vault_dashboard_plan_operation(
    body: dict,
    a: Agent = Depends(get_agent),
    store: SessionStore = Depends(get_sessions),
) -> dict:
    """Generate a plan-only run for a chat-kind operation.

    Body: ``{folder, op_id}``. Same shape as ``/run-operation`` but the
    agent is instructed not to mutate state and to emit a fenced
    ``nexus-plan`` JSON array describing the steps it would take. UI
    subscribes to the resulting session, parses the plan from the agent's
    last assistant message, and shows it for approval.
    """
    folder = body.get("folder")
    op_id = body.get("op_id") or body.get("operation_id")
    return await _kick_chat_operation(
        folder=folder,
        op_id=op_id,
        plan_only=True,
        approved_plan=None,
        a=a,
        store=store,
    )


@router.post(
    "/vault/dashboard/run-operation/execute",
    status_code=status.HTTP_201_CREATED,
)
async def vault_dashboard_execute_operation(
    body: dict,
    a: Agent = Depends(get_agent),
    store: SessionStore = Depends(get_sessions),
) -> dict:
    """Execute a chat-kind operation against a user-approved plan.

    Body: ``{folder, op_id, approved_plan}`` where ``approved_plan`` is the
    raw JSON string the user approved (potentially edited from the original
    plan output). The agent is told the plan was approved and asked to
    execute it.
    """
    folder = body.get("folder")
    op_id = body.get("op_id") or body.get("operation_id")
    approved_plan = body.get("approved_plan")
    if not isinstance(approved_plan, str) or not approved_plan.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`approved_plan` (JSON string) is required",
        )
    return await _kick_chat_operation(
        folder=folder,
        op_id=op_id,
        plan_only=False,
        approved_plan=approved_plan,
        a=a,
        store=store,
    )


_PLAN_INSTRUCTIONS = (
    "PLAN-ONLY MODE — this is a dry run. The user wants to see what you "
    "would do before authorising the real run. Rules:\n"
    "- You MAY call **read-only** tools (read tables, search, vault read) "
    "to figure out what you'd actually do.\n"
    "- You MUST NOT call any tool that modifies state: no row writes, no "
    "vault writes, no skill edits, no terminal commands. If unsure whether "
    "a tool writes, don't call it.\n"
    "- DO NOT execute the operation. Plan only.\n"
    "- Output exactly one fenced JSON block tagged `nexus-plan` containing "
    "an array of intended steps. Each step: "
    "`{action: string, target?: string, detail?: string, mutates: bool}`. "
    "Use `mutates: true` for any step that would write/modify data; the UI "
    "highlights those for the user.\n"
    "- After the JSON block, write 1-2 sentences inviting approval or "
    "refinement. No prose before the block.\n"
    "Example:\n"
    "```nexus-plan\n"
    "[\n"
    "  {\"action\": \"read\", \"target\": \"clinic-data/Patients.md\", "
    "\"mutates\": false},\n"
    "  {\"action\": \"add row\", \"target\": "
    "\"clinic-data/EventSchedulings.md\", \"detail\": "
    "\"new visit for patient P003 on 2026-05-10\", \"mutates\": true}\n"
    "]\n"
    "```"
)


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


_WIDGET_DESIGN_INSTRUCTIONS = (
    "You are designing a **dashboard widget**. Your job is to produce a "
    "SQL query and a visualization config that the system will save and "
    "re-execute on every refresh WITHOUT calling the LLM again.\n\n"
    "Available visualization types:\n"
    "- `bar` — bar chart (vertical/horizontal, stacked)\n"
    "- `line` — line chart (trends, multi-series)\n"
    "- `area` — area chart (volume, stacked)\n"
    "- `pie` / `donut` — pie or donut chart (part-to-whole)\n"
    "- `table` — structured data table\n"
    "- `kpi` — single metric card with optional trend\n\n"
    "Steps:\n"
    "1. Use `datatable_manage` with action=`view` to inspect the schema "
    "of tables in this database. Understand columns, types, and relations.\n"
    "2. Write a DuckDB SQL query that extracts exactly the data needed. "
    "Reference tables by their filename without `.md` (e.g. `sales` not "
    "`sales.md`). Only SELECT/WITH queries are allowed.\n\n"
    "DuckDB SQL pitfalls — IMPORTANT:\n"
    "- strftime does NOT support `%q` or `%Q` for quarters. To group by "
    "quarter use: `'Q' || CAST(EXTRACT(QUARTER FROM col) AS VARCHAR) || "
    "' ' || CAST(EXTRACT(YEAR FROM col) AS VARCHAR)` or just "
    "'EXTRACT(YEAR FROM col) || \\x27-Q\\x27 || EXTRACT(QUARTER FROM col)'.\n"
    "- strftime format specifiers: `%Y` year, `%m` month, `%d` day, `%H` "
    "hour, `%M` minute, `%S` second. Use `EXTRACT()` for anything else.\n"
    "- Date math: use `col - INTERVAL '30 days'`, `DATE_TRUNC('month', col)`, "
    "etc.\n"
    "- Do NOT use table alias `t` in your query — unlike datatable_manage, "
    "the widget system uses the actual table filename as the table name "
    "(e.g., `encounters` not `t` for encounters.md).\n\n"
    "3. Pick the best `viz_type` from the list above.\n"
    "4. Output EXACTLY ONE fenced JSON block tagged `nexus-widget-plan` "
    "with this shape:\n"
    "```nexus-widget-plan\n"
    "{\n"
    '  "title": "short widget title",\n'
    '  "viz_type": "bar",\n'
    '  "query": "SELECT ... FROM table_name ...",\n'
    '  "query_tables": ["table_name.md"],\n'
    '  "viz_config": {\n'
    '    "x_field": "column_name",\n'
    '    "y_field": "column_name",\n'
    '    "y_label": "optional axis label",\n'
    '    "x_label": "optional axis label"\n'
    "  }\n"
    "}\n```\n\n"
    "viz_config guidance:\n"
    "- For `bar`/`line`/`area`: `x_field` (labels), `y_field` (values). "
    "Multi-series: use `y_fields` array. `stacked: true` for stacked.\n"
    "- For `pie`/`donut`: `x_field` (slice labels), `y_field` (slice values).\n"
    "- For `table`: no special config needed — all columns render.\n"
    "- For `kpi`: `y_field` (the metric), `label_field` (label text). "
    "Optional `trend_field` for comparing current vs previous row.\n\n"
    "After the JSON block, write 1-2 sentences explaining the widget. "
    "Do NOT call `ask_user`. Do NOT use tools after producing the plan."
)


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
    """Return the widget's current query result (JSON). Empty if not refreshed yet."""
    from ... import vault_widgets
    try:
        body = vault_widgets.read_widget_result(folder, widget_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc),
        )
    return {"folder": folder, "widget_id": widget_id, "content": body}


@router.post(
    "/vault/dashboard/widgets/preview",
    status_code=status.HTTP_200_OK,
)
async def vault_dashboard_preview_widget(body: dict) -> dict:
    """Execute a widget query without persisting anything.

    Body: ``{folder, query, viz_type, viz_config?, query_tables?}``.
    Runs the SQL against the folder's data-tables via DuckDB and returns
    the structured result. No widget is created or modified — pure read.
    """
    folder = body.get("folder", "")
    query = (body.get("query") or "").strip()
    if not isinstance(folder, str) or not folder:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`folder` required",
        )
    if not query:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`query` required",
        )

    from ...widget_query import execute_widget_query

    query_tables = body.get("query_tables")
    if isinstance(query_tables, str):
        query_tables = [query_tables]

    result = execute_widget_query(folder, query, query_tables=query_tables)
    return {"result": result}


@router.post(
    "/vault/dashboard/widgets/{widget_id}/execute",
    status_code=status.HTTP_200_OK,
)
async def vault_dashboard_execute_widget(
    widget_id: str,
    body: dict,
) -> dict:
    """Execute the widget's saved SQL query and return structured JSON.

    Body: ``{folder}``. Reads the widget config, runs the stored query
    against DuckDB with the folder's data-tables, persists the result
    to ``<folder>/_widgets/<widget_id>.json``, and stamps
    ``last_refreshed_at``. No LLM involved — fast, cheap, deterministic.
    """
    from datetime import datetime, timezone
    from ... import vault_dashboard, vault_widgets
    from ...widget_query import execute_widget_query

    folder = body.get("folder", "")
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

    query = widget.get("query", "")
    if not query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"widget {widget_id!r} has no query to execute",
        )

    result = execute_widget_query(
        folder,
        query,
        query_tables=widget.get("query_tables"),
    )

    if "error" in result and result.get("row_count", 0) == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=result["error"],
        )

    import json
    vault_widgets.write_widget_result(folder, widget_id, json.dumps(result))

    try:
        now = datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ).replace("+00:00", "Z")
        vault_dashboard.set_widget_refreshed(folder, widget_id, now)
    except Exception:
        log.exception("widget execute: timestamp stamp failed")

    return {
        "folder": folder,
        "widget_id": widget_id,
        "result": result,
        "executed_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ).replace("+00:00", "Z"),
    }


def _widget_context_prefix(folder: str) -> str:
    return f"Widget: {folder}#"


@router.post(
    "/vault/dashboard/widgets/{widget_id}/refresh",
    status_code=status.HTTP_200_OK,
)
async def vault_dashboard_refresh_widget(
    widget_id: str,
    body: dict,
) -> dict:
    """Execute the widget's saved SQL query (no LLM).

    Alias for ``/execute`` — kept for backward compatibility with the
    UI's refresh button wiring. Body: ``{folder}``.
    """
    return await vault_dashboard_execute_widget(widget_id, body)


@router.post(
    "/vault/dashboard/widgets/{widget_id}/design",
    status_code=status.HTTP_201_CREATED,
)
async def vault_dashboard_design_widget(
    widget_id: str,
    body: dict,
    a: Agent = Depends(get_agent),
    store: SessionStore = Depends(get_sessions),
) -> dict:
    """Run an LLM session to design a widget's query + visualization.

    Body: ``{folder, goal}``. The LLM inspects the database schema and
    produces a ``nexus-widget-plan`` JSON fence with query, viz_type, and
    viz_config. The UI parses the plan, previews it, and saves it via
    ``POST /vault/dashboard/widgets`` (upsert).

    Returns ``{session_id}`` so the UI can stream the LLM's reply and
    subscribe to ``op_done``.
    """
    from .vault_dispatch import HIDDEN_SEED_MARKER, _resolve_dispatch_model
    from .vault_dispatch_helpers import run_background_agent_turn
    from ..events import SessionEvent

    folder = body.get("folder", "")
    goal = (body.get("goal") or "").strip()
    if not isinstance(folder, str):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`folder` required",
        )

    from ... import vault_dashboard
    dashboard = vault_dashboard.read_dashboard(folder)
    db_title = (dashboard.get("title") or folder or "(root)").strip()
    widget = next(
        (w for w in dashboard.get("widgets") or [] if w.get("id") == widget_id),
        None,
    )
    widget_title = widget.get("title", widget_id) if widget else widget_id

    seed_lines = [
        HIDDEN_SEED_MARKER.rstrip(),
        _WIDGET_DESIGN_INSTRUCTIONS,
        "",
        f"You are designing widget **{widget_title}** on database "
        f"**{db_title}** (vault folder `{folder or '/'}`).",
        "",
        "User's goal:",
        goal or f"Design the best widget for \"{widget_title}\"",
    ]
    seed_message = "\n".join(seed_lines)

    session = store.create(context=f"{_widget_context_prefix(folder)}{widget_id}")
    try:
        store.mark_hidden(session.id)
    except Exception:
        log.exception("widget design: mark_hidden failed")
    try:
        store.rename(session.id, f"✨ Design: {widget_title}"[:60])
    except Exception:
        log.exception("widget design: title rename failed")

    resolved_model = _resolve_dispatch_model(None, a)

    async def _run_design() -> None:
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
        try:
            store.publish(
                session.id,
                SessionEvent(
                    kind="op_done",
                    data={"status": outcome, "error": error_msg},
                ),
            )
        except Exception:
            log.exception("widget design: terminal event publish failed")

    asyncio.create_task(_run_design())

    return {
        "session_id": session.id,
        "folder": folder,
        "widget_id": widget_id,
        "status": "running",
    }


_WIZARD_SYSTEM_PROMPTS: dict[str, str] = {
    "widget": (
        "You are a **dashboard-widget design assistant**. Your job is to help "
        "the user define ONE widget for the database dashboard, then hand it "
        "off to the system that will create it.\n\n"
        "How widgets work:\n"
        "- A widget has: `title` (short label), `viz_type` (`bar` | `line` | "
        "`area` | `pie` | `donut` | `table` | `kpi`), `query` (DuckDB SQL), "
        "`query_tables` (list of .md filenames referenced), `viz_config` "
        "(field mappings, labels), `refresh` (`daily` | `manual`), and "
        "`size` (`sm` | `md` | `lg`).\n"
        "- `bar`/`line`/`area` — chart with x_field, y_field (or y_fields "
        "for multi-series). Best for trends and comparisons.\n"
        "- `pie`/`donut` — part-to-whole chart. x_field for labels, y_field "
        "for values.\n"
        "- `table` — structured data table. No special viz_config needed.\n"
        "- `kpi` — single metric card with optional trend. y_field for value, "
        "label_field for the label.\n\n"
        "How to behave:\n"
        "1. Read the user's goal. Use `datatable_manage` with action=`view` "
        "to inspect table schemas so you can write correct SQL.\n"
        "2. If something is genuinely ambiguous (e.g. which table to read, "
        "which column to plot, time window), ask **at most ONE concise "
        "clarifying question per turn**. Don't ask more than 2 questions "
        "total — pick the highest-value missing piece, then propose with "
        "reasonable defaults for the rest.\n\n"
        "DuckDB SQL pitfalls — IMPORTANT:\n"
        "- strftime does NOT support `%q` or `%Q` for quarters. To group by "
        "quarter use: `'Q' || CAST(EXTRACT(QUARTER FROM col) AS VARCHAR) || "
        "' ' || CAST(EXTRACT(YEAR FROM col) AS VARCHAR)` or "
        "'EXTRACT(YEAR FROM col) || \\x27-Q\\x27 || EXTRACT(QUARTER FROM col)'.\n"
        "- strftime format specifiers: `%Y` year, `%m` month, `%d` day, "
        "`%H` hour, `%M` minute, `%S` second. Use `EXTRACT()` for anything "
        "else.\n"
        "- Date math: use `col - INTERVAL '30 days'`, "
        "`DATE_TRUNC('month', col)`, etc.\n"
        "- Do NOT use table alias `t` in your query — unlike datatable_manage, "
        "the widget system uses the actual table filename as the table name "
        "(e.g., `encounters` not `t` for encounters.md).\n\n"
        "3. When ready to propose, output exactly one fenced JSON block with "
        "language tag `nexus-widget-proposal` containing the full widget "
        "config. Include a `summary` field with one plain-English sentence "
        "describing what the widget shows (the user will see this, not the "
        "SQL). If the data shape supports other visualization types besides "
        "your primary pick, include an `alternatives` array listing them "
        "(e.g. `[\"line\", \"table\"]`). Omit `alternatives` if only one "
        "viz type makes sense. Example:\n"
        "```nexus-widget-proposal\n"
        "{\n  \"title\": \"Monthly Revenue\",\n  \"viz_type\": \"bar\",\n  "
        "\"query\": \"SELECT strftime(date, '%Y-%m') AS month, SUM(amount) "
        "AS total FROM sales GROUP BY month ORDER BY month\",\n  "
        "\"query_tables\": [\"sales.md\"],\n  \"viz_config\": {\"x_field\": "
        "\"month\", \"y_field\": \"total\", \"y_label\": \"Revenue ($)\"},\n  "
        "\"refresh\": \"daily\",\n  \"size\": \"md\",\n  "
        "\"summary\": \"A bar chart showing total revenue grouped by month "
        "across all sales.\",\n  \"alternatives\": [\"line\", \"table\"]\n}\n"
        "```\n"
        "4. After proposing, write one short sentence inviting the user to "
        "approve, tweak, or ask for changes. Do NOT mention SQL, column "
        "names, or query syntax in your prose — speak in plain language "
        "about what the widget shows. Do NOT call `ask_user`. The system "
        "will create the widget when the user clicks Approve."
    ),
    "operation": (
        "You are a **dashboard-operation design assistant**. Your job is to "
        "help the user define ONE quick action for the database dashboard.\n\n"
        "How operations work:\n"
        "- An operation has: `label` (button text), `kind` (`chat` | "
        "`form`), and either `prompt` (chat kind: instructions the agent "
        "runs) or `table` (form kind: vault path to the target table) plus "
        "optional `prefill` (default values for form-kind inputs).\n"
        "- `chat` runs an agent turn and shows the reply in a popup. Best "
        "for read-only summaries, ad-hoc queries, or write-once-and-confirm.\n"
        "- `form` opens a pre-filled add-row form against a target table. "
        "Best for repetitive data entry (\"Add patient visit\").\n\n"
        "How to behave:\n"
        "1. Read the user's goal.\n"
        "2. Ask AT MOST ONE concise clarifying question per turn, total no "
        "more than 2 questions before proposing.\n"
        "3. When ready, output exactly one fenced JSON block tagged "
        "`nexus-operation-proposal` with the config. Examples:\n"
        "```nexus-operation-proposal\n"
        "{\n  \"label\": \"Last patient seen\",\n  \"kind\": \"chat\",\n  "
        "\"prompt\": \"Find the most recent patient visit and summarize it.\"\n}\n```\n"
        "```nexus-operation-proposal\n"
        "{\n  \"label\": \"Add prescription\",\n  \"kind\": \"form\",\n  "
        "\"table\": \"DemoClinic/Prescriptions.md\",\n  \"prefill\": {}\n}\n```\n"
        "4. After proposing, invite the user to approve or tweak. Do NOT "
        "call tools — design only."
    ),
}


@router.post("/vault/dashboard/wizard/start", status_code=status.HTTP_201_CREATED)
async def vault_dashboard_wizard_start(
    body: dict,
    a: Agent = Depends(get_agent),
    store: SessionStore = Depends(get_sessions),
) -> dict:
    """Start a wizard chat session for designing a widget or operation.

    Body: ``{folder, kind: "widget" | "operation", goal}``.

    Creates a hidden session seeded with the wizard's role + the user's
    initial goal, kicks the first turn in the background. Subsequent turns
    go through the regular ``/chat/stream`` endpoint with the returned
    ``session_id``.

    Returns ``{session_id}`` so the UI can subscribe to events and stream
    the wizard's reply.
    """
    from .vault_dispatch import HIDDEN_SEED_MARKER, _resolve_dispatch_model
    from .vault_dispatch_helpers import run_background_agent_turn
    from ..events import SessionEvent

    folder = body.get("folder", "")
    kind = body.get("kind")
    goal = (body.get("goal") or "").strip()
    if kind not in _WIZARD_SYSTEM_PROMPTS or not goal:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`kind` must be 'widget' or 'operation' and `goal` is required",
        )
    if not isinstance(folder, str):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`folder` must be a string",
        )

    from ... import vault_dashboard
    dashboard = vault_dashboard.read_dashboard(folder)
    db_title = (dashboard.get("title") or folder or "(root)").strip()

    seed_lines = [
        HIDDEN_SEED_MARKER.rstrip(),
        _WIZARD_SYSTEM_PROMPTS[kind],
        "",
        f"Database: **{db_title}** (vault folder `{folder or '/'}`).",
        "",
        "User's initial goal:",
        goal,
    ]
    seed_message = "\n".join(seed_lines)

    session = store.create(context=f"Dashboard wizard: {folder}#{kind}")
    try:
        store.mark_hidden(session.id)
    except Exception:
        log.exception("wizard start: mark_hidden failed")
    try:
        store.rename(session.id, f"✨ {kind} wizard"[:60])
    except Exception:
        log.exception("wizard start: title rename failed")

    resolved_model = _resolve_dispatch_model(None, a)

    async def _run_first_turn() -> None:
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
        try:
            store.publish(
                session.id,
                SessionEvent(
                    kind="op_done",
                    data={"status": outcome, "error": error_msg},
                ),
            )
        except Exception:
            log.exception("wizard start: terminal event publish failed")

    asyncio.create_task(_run_first_turn())

    return {
        "session_id": session.id,
        "folder": folder,
        "kind": kind,
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
