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
        "kpi cards), custom screens, multi-step flows, linked boards/calendars, "
        "a chat session id bound to that database, and an optional title. "
        "Actions: view, set_operations, add_operation, remove_operation, "
        "set_widgets, add_widget, remove_widget, add_screen, remove_screen, "
        "add_flow, remove_flow, add_link, remove_link, "
        "set_chat_session, set_title, delete_database.\n\n"
        "Screens define custom UI layouts for interacting with data: "
        "single-form, master-detail, search-and-edit, list-with-preview, or dashboard. "
        "Each screen has sections (panels) that reference data tables and display fields.\n\n"
        "Flows define multi-step processes: a sequence of form fills, searches, and "
        "confirmations that guide users through business processes.\n\n"
        "Links connect kanban boards and calendars that live in the same folder.\n\n"
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
                    "add_screen",
                    "remove_screen",
                    "add_flow",
                    "remove_flow",
                    "add_link",
                    "remove_link",
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
            "screen": {
                "type": "object",
                "description": (
                    "Screen definition for add_screen. Required: id, name, layout "
                    "('single-form'|'master-detail'|'search-and-edit'|'list-with-preview'|'dashboard'). "
                    "Optional: description, sections, actions, flows.\n\n"
                    "Sections structure:\n"
                    "- id: section identifier\n"
                    "- source: {table: './filename.md'} — vault-relative path to a data table\n"
                    "- display_fields: list of column names to show\n"
                    "- search_fields: list of columns to use for search (optional)\n\n"
                    "For master-detail screens, the second section needs a 'relation' object:\n"
                    "- relation: {field: '<fk_column>'} — the FK column on the child table that "
                    "points to the parent. The UI auto-filters child rows by the selected parent.\n\n"
                    "Example master-detail screen (purchases → items):\n"
                    '{"id": "purchase-detail", "name": "Purchase Detail", "layout": "master-detail", '
                    '"sections": ['
                    '{"id": "purchases", "source": {"table": "./purchases.md"}, '
                    '"display_fields": ["date", "supplier", "total", "status"]}, '
                    '{"id": "items", "source": {"table": "./purchase_items.md"}, '
                    '"display_fields": ["product", "quantity", "unit_price", "subtotal"], '
                    '"relation": {"field": "purchase"}}'
                    ']}'
                ),
            },
            "screen_id": {
                "type": "string",
                "description": "Screen id for remove_screen.",
            },
            "flow": {
                "type": "object",
                "description": (
                    "Flow definition for add_flow. Required: id, name. "
                    "Optional: steps (array of step objects).\n\n"
                    "Step types:\n"
                    "- {type: 'form', table: './file.md', fields: [...], prefill: {...}}\n"
                    "  Creates a single row in the target table.\n"
                    "- {type: 'repeatable-form', table: './file.md', parent_ref: {step: 0, field: 'fk_col'}}\n"
                    "  Adds N rows to the target table. The parent_ref auto-populates 'field' on "
                    "every row with the ID of the record created in step N. "
                    "Use this for 'create parent + N children' workflows (e.g. purchase + items).\n"
                    "- {type: 'confirm', message: '...'}\n"
                    "  Shows a confirmation message before finishing.\n"
                    "- {type: 'search', table: './file.md', message: '...'}\n"
                    "  Search and select a record.\n\n"
                    "Example — Record Purchase with items:\n"
                    '{"id": "record-purchase", "name": "Record Purchase", "steps": ['
                    '{"type": "form", "table": "./purchases.md", "fields": ["date", "supplier"]}, '
                    '{"type": "repeatable-form", "table": "./purchase_items.md", '
                    '"parent_ref": {"step": 0, "field": "purchase"}}, '
                    '{"type": "confirm", "message": "Confirm this purchase?"}]}'
                ),
            },
            "flow_id": {
                "type": "string",
                "description": "Flow id for remove_flow.",
            },
            "link_kind": {
                "type": "string",
                "enum": ["boards", "calendars"],
                "description": "Kind of link for add_link/remove_link.",
            },
            "link_path": {
                "type": "string",
                "description": "Vault-relative path for add_link/remove_link (e.g. './workflow.md').",
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

        if action == "add_screen":
            screen = args.get("screen")
            if not isinstance(screen, dict):
                return json.dumps({"ok": False, "error": "`screen` (object) required"})
            patched = vault_dashboard.upsert_screen(folder, screen)
            return json.dumps({"ok": True, "dashboard": patched})

        if action == "remove_screen":
            screen_id = args.get("screen_id", "")
            if not screen_id:
                return json.dumps({"ok": False, "error": "`screen_id` required"})
            patched = vault_dashboard.remove_screen(folder, screen_id)
            return json.dumps({"ok": True, "dashboard": patched})

        if action == "add_flow":
            flow = args.get("flow")
            if not isinstance(flow, dict):
                return json.dumps({"ok": False, "error": "`flow` (object) required"})
            patched = vault_dashboard.upsert_flow(folder, flow)
            return json.dumps({"ok": True, "dashboard": patched})

        if action == "remove_flow":
            flow_id = args.get("flow_id", "")
            if not flow_id:
                return json.dumps({"ok": False, "error": "`flow_id` required"})
            patched = vault_dashboard.remove_flow(folder, flow_id)
            return json.dumps({"ok": True, "dashboard": patched})

        if action == "add_link":
            link_kind = args.get("link_kind", "")
            link_path = args.get("link_path", "")
            if not link_kind or not link_path:
                return json.dumps({"ok": False, "error": "`link_kind` and `link_path` required"})
            patched = vault_dashboard.add_link(folder, link_kind, link_path)
            return json.dumps({"ok": True, "dashboard": patched})

        if action == "remove_link":
            link_kind = args.get("link_kind", "")
            link_path = args.get("link_path", "")
            if not link_kind or not link_path:
                return json.dumps({"ok": False, "error": "`link_kind` and `link_path` required"})
            patched = vault_dashboard.remove_link(folder, link_kind, link_path)
            return json.dumps({"ok": True, "dashboard": patched})

        return json.dumps({"ok": False, "error": f"unknown action: {action!r}"})

    except (KeyError, ValueError, FileNotFoundError, OSError) as exc:
        return json.dumps({"ok": False, "error": str(exc)})
