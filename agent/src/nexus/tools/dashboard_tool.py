"""Data-dashboard agent tool: dashboard_manage.

Pure CRUD over the per-database `_data.md` marker file. No embedded LLM
inference — the agent itself reasons about *which* operations to seed
(via the `database-design` skill) and calls this tool to persist them.
"""

from __future__ import annotations

import json
from typing import Any

from ..agent.llm import ToolSpec

DASHBOARD_MANAGE_TOOL = ToolSpec(
    name="dashboard_manage",
    description=(
        "Manage per-database dashboards stored as `_data.md` markdown files in "
        "the vault (one per folder containing data-tables). Each dashboard "
        "holds quick-action operations (chat or form), widgets (chart/report/"
        "kpi cards), a chat session id bound to that database, and an "
        "optional title. "
        "Actions: view, set_operations, add_operation, remove_operation, "
        "set_widgets, add_widget, remove_widget, set_chat_session, set_title, "
        "delete_database.\n\n"
        "Discovery: dashboards live alongside datatables. Start by calling "
        "`datatable_manage action=list_databases` to identify database folders, "
        "then call `dashboard_manage action=view folder=<folder>` to inspect.\n\n"
        "Safe usage pattern:\n"
        "- Before `set_operations`/`set_widgets` (which replace all items), call `view` "
        "first to inspect current state and decide what to keep vs. change.\n"
        "- `delete_database` is irreversible and deletes the `_data.md` file; requires "
        "an explicit `confirm` parameter matching the folder basename."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "view",
                    "set_operations",
                    "add_operation",
                    "remove_operation",
                    "set_widgets",
                    "add_widget",
                    "remove_widget",
                    "set_chat_session",
                    "set_title",
                    "delete_database",
                ],
            },
            "folder": {
                "type": "string",
                "description": "Vault-relative folder of the database (e.g. 'shop'). Use '' for the vault root.",
            },
            "operations": {
                "type": "array",
                "items": {"type": "object"},
                "description": (
                    "List of operations for set_operations. Each: "
                    "{id (slug), label, kind: 'chat'|'form', prompt, table? (form only), prefill? (form only), icon?, order?}"
                ),
            },
            "operation": {
                "type": "object",
                "description": "Single operation for add_operation. Same shape as `operations` items.",
            },
            "op_id": {
                "type": "string",
                "description": "Operation id for remove_operation.",
            },
            "widgets": {
                "type": "array",
                "items": {"type": "object"},
                "description": (
                    "List of widgets for set_widgets. Each widget MUST include: "
                    "id (slug), title, viz_type ('bar'|'line'|'area'|'pie'|'donut'|'table'|'kpi'), "
                    "query (DuckDB SQL — SELECT/WITH only, reference tables by filename "
                    "without .md), query_tables (list of .md filenames, e.g. ['sales.md']), "
                    "viz_config: {x_field, y_field, y_label?, x_label?, stacked?} "
                    "(not needed for 'table' viz_type), "
                    "refresh ('manual'|'daily'), size? ('sm'|'md'|'lg'), order?. "
                    "The query is executed against DuckDB on refresh — no LLM involved. "
                    "You MUST write the actual SQL; do NOT use a 'prompt' instead of a query."
                ),
            },
            "widget": {
                "type": "object",
                "description": (
                    "Single widget for add_widget. Same shape as `widgets` items. "
                    "Required: id, title, viz_type, query (DuckDB SQL). "
                    "Use datatable_manage action=view to inspect table schemas before writing SQL."
                ),
            },
            "widget_id": {
                "type": "string",
                "description": "Widget id for remove_widget.",
            },
            "session_id": {
                "type": ["string", "null"],
                "description": "Chat session id for set_chat_session (null clears).",
            },
            "title": {
                "type": "string",
                "description": "Display title for set_title.",
            },
            "confirm": {
                "type": "string",
                "description": (
                    "For delete_database: must equal the folder's basename (e.g. 'shop'). "
                    "Server-side guard against accidental wipes."
                ),
            },
        },
        "required": ["action"],
    },
)


def _execute_widget_sql(folder: str, widget: dict[str, Any]) -> dict[str, Any] | None:
    from ..widget_query import execute_widget_query

    query = widget.get("query", "")
    if not isinstance(query, str) or not query.strip():
        return None
    return execute_widget_query(folder, query, query_tables=widget.get("query_tables"))


def handle_dashboard_tool(args: dict[str, Any]) -> str:
    """Dispatch the requested dashboard action and return serialized JSON."""
    from .. import vault_dashboard, vault_widgets

    action = args.get("action", "")
    folder = args.get("folder", "")
    if not isinstance(folder, str):
        return json.dumps({"ok": False, "error": "`folder` must be a string"})

    try:
        if action == "view":
            return json.dumps({"ok": True, "dashboard": vault_dashboard.read_dashboard(folder)})

        if action == "set_operations":
            ops = args.get("operations")
            if not isinstance(ops, list):
                return json.dumps({"ok": False, "error": "`operations` (list) required"})
            patched = vault_dashboard.patch_dashboard(folder, {"operations": ops})
            return json.dumps({"ok": True, "dashboard": patched})

        if action == "add_operation":
            op = args.get("operation")
            if not isinstance(op, dict):
                return json.dumps({"ok": False, "error": "`operation` (object) required"})
            patched = vault_dashboard.upsert_operation(folder, op)
            return json.dumps({"ok": True, "dashboard": patched})

        if action == "remove_operation":
            op_id = args.get("op_id", "")
            if not op_id:
                return json.dumps({"ok": False, "error": "`op_id` required"})
            patched = vault_dashboard.delete_operation(folder, op_id)
            return json.dumps({"ok": True, "dashboard": patched})

        if action == "set_widgets":
            widgets = args.get("widgets")
            if not isinstance(widgets, list):
                return json.dumps({"ok": False, "error": "`widgets` (list) required"})
            patched = vault_dashboard.patch_dashboard(folder, {"widgets": widgets})
            errors = {}
            for w in widgets:
                if not isinstance(w, dict):
                    continue
                result = _execute_widget_sql(folder, w)
                if result and result.get("error"):
                    errors[w.get("id", "?")] = result["error"]
                elif result:
                    wid = w.get("id", "")
                    if wid:
                        vault_widgets.write_widget_result(folder, wid, json.dumps(result))
            resp: dict[str, Any] = {"ok": True, "dashboard": patched}
            if errors:
                resp["sql_errors"] = errors
            return json.dumps(resp)

        if action == "add_widget":
            widget = args.get("widget")
            if not isinstance(widget, dict):
                return json.dumps({"ok": False, "error": "`widget` (object) required"})
            patched = vault_dashboard.upsert_widget(folder, widget)
            result = _execute_widget_sql(folder, widget)
            resp = {"ok": True, "dashboard": patched}
            if result and result.get("error"):
                resp["sql_error"] = result["error"]
            elif result:
                wid = widget.get("id", "")
                if wid:
                    vault_widgets.write_widget_result(folder, wid, json.dumps(result))
                resp["execution_preview"] = {
                    "row_count": result.get("row_count", 0),
                    "columns": result.get("columns", []),
                }
            return json.dumps(resp)

        if action == "remove_widget":
            widget_id = args.get("widget_id", "")
            if not widget_id:
                return json.dumps({"ok": False, "error": "`widget_id` required"})
            patched = vault_dashboard.delete_widget(folder, widget_id)
            return json.dumps({"ok": True, "dashboard": patched})

        if action == "set_chat_session":
            sid = args.get("session_id")
            patched = vault_dashboard.set_chat_session(folder, sid if isinstance(sid, str) else None)
            return json.dumps({"ok": True, "dashboard": patched})

        if action == "set_title":
            title = args.get("title", "")
            if not title:
                return json.dumps({"ok": False, "error": "`title` required"})
            patched = vault_dashboard.patch_dashboard(folder, {"title": title})
            return json.dumps({"ok": True, "dashboard": patched})

        if action == "delete_database":
            confirm = args.get("confirm", "")
            res = vault_dashboard.delete_database(folder, confirm=confirm)
            return json.dumps({"ok": True, **res})

        return json.dumps({"ok": False, "error": f"unknown action: {action!r}"})

    except (KeyError, ValueError, FileNotFoundError, OSError) as exc:
        return json.dumps({"ok": False, "error": str(exc)})
