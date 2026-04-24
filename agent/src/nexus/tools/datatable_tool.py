"""Data-table agent tool: datatable_manage.

Operates on vault data-table files (markdown with ``data-table-plugin: basic``
frontmatter). The agent addresses tables by their vault-relative path.
"""

from __future__ import annotations

import json
from typing import Any

from ..agent.llm import ToolSpec

DATATABLE_MANAGE_TOOL = ToolSpec(
    name="datatable_manage",
    description=(
        "Manage structured data tables stored as markdown in the vault. "
        "Each table is a single .md file with `data-table-plugin: basic` frontmatter "
        "containing a Schema block (field definitions) and a Rows block (YAML list). "
        "Actions: create_table, view, add_row, update_row, delete_row, set_schema, list_rows."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "create_table", "view",
                    "add_row", "add_rows", "update_row", "delete_row",
                    "list_rows", "set_schema", "set_views",
                ],
                "description": "Action to perform.",
            },
            "path": {
                "type": "string",
                "description": "Vault-relative path to the data-table .md file (e.g. 'data/bugs.md').",
            },
            "schema": {
                "type": "object",
                "description": (
                    "Schema definition for create_table or set_schema. "
                    "Shape: { title?: str, fields: [{name, label?, kind?, required?, choices?, ...}] }. "
                    "field.kind: 'text'|'textarea'|'number'|'boolean'|'select'|'multiselect'|'date'."
                ),
            },
            "row": {
                "type": "object",
                "description": "Row data for add_row or update_row. Key-value pairs matching field names.",
            },
            "rows": {
                "type": "array",
                "description": "List of rows for add_rows (bulk import). Each item is a row dict.",
                "items": {"type": "object"},
            },
            "row_id": {
                "type": "string",
                "description": "Row identifier (_id field) for update_row or delete_row.",
            },
            "views": {
                "type": "array",
                "description": (
                    "List of saved view presets for set_views. "
                    "Each view: { name: str, filter?: str, sort?: {field, dir: 'asc'|'desc'}, hidden?: [field_names] }"
                ),
                "items": {"type": "object"},
            },
        },
        "required": ["action", "path"],
    },
)


def handle_datatable_tool(args: dict[str, Any]) -> str:
    from .. import vault_datatable

    action = args.get("action", "")
    path = args.get("path", "")
    if not path:
        return json.dumps({"ok": False, "error": "`path` is required"})

    try:
        if action == "create_table":
            schema = args.get("schema")
            if not schema or not isinstance(schema, dict):
                return json.dumps({"ok": False, "error": "`schema` is required for create_table"})
            tbl = vault_datatable.create_table(path, schema)
            return json.dumps({"ok": True, "path": path, "table": tbl})

        if action == "view":
            tbl = vault_datatable.read_table(path)
            return json.dumps({"ok": True, "path": path, "table": tbl})

        if action == "list_rows":
            tbl = vault_datatable.read_table(path)
            return json.dumps({"ok": True, "rows": tbl["rows"], "count": len(tbl["rows"])})

        if action == "add_row":
            row = args.get("row")
            if not row or not isinstance(row, dict):
                return json.dumps({"ok": False, "error": "`row` is required for add_row"})
            added = vault_datatable.add_row(path, row)
            return json.dumps({"ok": True, "row": added})

        if action == "update_row":
            row_id = args.get("row_id", "")
            row = args.get("row")
            if not row_id:
                return json.dumps({"ok": False, "error": "`row_id` is required for update_row"})
            if not row or not isinstance(row, dict):
                return json.dumps({"ok": False, "error": "`row` is required for update_row"})
            updated = vault_datatable.update_row(path, row_id, row)
            return json.dumps({"ok": True, "row": updated})

        if action == "delete_row":
            row_id = args.get("row_id", "")
            if not row_id:
                return json.dumps({"ok": False, "error": "`row_id` is required for delete_row"})
            vault_datatable.delete_row(path, row_id)
            return json.dumps({"ok": True})

        if action == "set_schema":
            schema = args.get("schema")
            if not schema or not isinstance(schema, dict):
                return json.dumps({"ok": False, "error": "`schema` is required for set_schema"})
            tbl = vault_datatable.set_schema(path, schema)
            return json.dumps({"ok": True, "table": tbl})

        if action == "add_rows":
            rows = args.get("rows")
            if not isinstance(rows, list):
                return json.dumps({"ok": False, "error": "`rows` (list) is required for add_rows"})
            added = vault_datatable.add_rows(path, rows)
            return json.dumps({"ok": True, "added": added, "count": len(added)})

        if action == "set_views":
            views = args.get("views")
            if not isinstance(views, list):
                return json.dumps({"ok": False, "error": "`views` (list) is required for set_views"})
            tbl = vault_datatable.set_views(path, views)
            return json.dumps({"ok": True, "table": tbl})

        return json.dumps({"ok": False, "error": f"unknown action: {action!r}"})

    except (KeyError, ValueError, FileNotFoundError, OSError) as exc:
        return json.dumps({"ok": False, "error": str(exc)})
