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
        "Tables in the same folder form a 'database'; fields can declare typed "
        "relations to other tables via `kind: ref` + `target_table`. "
        "Actions: create_table, view, add_row, add_rows, update_row, delete_row, "
        "list_rows (paginates: `limit` default 100, max 1000; `offset` default 0; "
        "response carries `total`, `truncated` so you can iterate), "
        "set_schema, set_views, add_field, remove_field, rename_field, "
        "create_relation, create_junction, suggest_schema, er_diagram, "
        "list_databases, related_rows."
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
                    "add_field", "remove_field", "rename_field",
                    "create_relation", "create_junction",
                    "suggest_schema", "er_diagram",
                    "list_databases", "related_rows",
                ],
                "description": "Action to perform.",
            },
            "path": {
                "type": "string",
                "description": "Vault-relative path to the data-table .md file (e.g. 'data/bugs.md'). Required by most actions; omitted by list_databases / suggest_schema / er_diagram (which take `folder`).",
            },
            "folder": {
                "type": "string",
                "description": "Vault-relative folder path for er_diagram or suggest_schema (use '' for vault root).",
            },
            "schema": {
                "type": "object",
                "description": (
                    "Schema definition for create_table or set_schema. "
                    "Shape: { title?: str, table?: { primary_key?: str, is_junction?: bool }, "
                    "fields: [{name, label?, kind?, required?, choices?, target_table?, cardinality?, ...}] }. "
                    "field.kind: 'text'|'textarea'|'number'|'boolean'|'select'|'multiselect'|'date'|'vault-link'|'formula'|'ref'. "
                    "For kind='ref', set target_table to the related table's vault path "
                    "(./sibling.md or ../folder/file.md) and cardinality to 'one' or 'many'."
                ),
            },
            "field": {
                "type": "object",
                "description": "Single field definition for add_field. Same shape as a schema.fields entry.",
            },
            "field_name": {
                "type": "string",
                "description": "Field name for remove_field, rename_field, create_relation.",
            },
            "new_name": {
                "type": "string",
                "description": "Target name for rename_field.",
            },
            "target_table": {
                "type": "string",
                "description": "Vault path of the table being referenced (used by create_relation).",
            },
            "cardinality": {
                "type": "string",
                "enum": ["one", "many"],
                "description": "Relation cardinality for create_relation; defaults to 'one'.",
            },
            "table_a": {
                "type": "string",
                "description": "First table for create_junction.",
            },
            "table_b": {
                "type": "string",
                "description": "Second table for create_junction.",
            },
            "description": {
                "type": "string",
                "description": "Free-text description of the user's data model for suggest_schema.",
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
                "description": "Row identifier (primary-key value, or _id) for update_row, delete_row, related_rows.",
            },
            "limit": {
                "type": "integer",
                "description": "Page size for list_rows (default 100, max 1000).",
            },
            "offset": {
                "type": "integer",
                "description": "Page offset for list_rows (default 0).",
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
        "required": ["action"],
    },
)


def handle_datatable_tool(args: dict[str, Any]) -> str:
    """Dispatch the requested data-table action and return serialized JSON.

    Args:
        args: Dict containing ``action`` and additional fields depending on the
              action (e.g. ``path``, ``schema``, ``row``, ``row_id``, ``rows``,
              ``views``, ``field``, ``field_name``, ``target_table``,
              ``cardinality``, ``table_a``, ``table_b``, ``folder``,
              ``description``).

    Returns:
        JSON with ``{"ok": true, ...}`` on success or ``{"ok": false, "error": ...}``
        for invalid arguments, missing files, or I/O errors.
    """
    from .. import vault_datatable
    from .. import vault_datatable_index

    action = args.get("action", "")
    path = args.get("path", "")

    # Folder-scoped actions don't need a path.
    folder_actions = {"er_diagram", "suggest_schema", "list_databases"}
    if action not in folder_actions and not path:
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
            all_rows = tbl["rows"]
            total = len(all_rows)
            offset = max(0, int(args.get("offset", 0)))
            limit_raw = args.get("limit")
            # Default 100, hard cap 1000 to keep payloads under ~100k tokens.
            limit = 100 if limit_raw is None else max(1, min(int(limit_raw), 1000))
            page = all_rows[offset : offset + limit]
            return json.dumps({
                "ok": True,
                "rows": page,
                "count": len(page),
                "total": total,
                "offset": offset,
                "limit": limit,
                "truncated": (offset + len(page)) < total,
            })

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

        if action == "add_field":
            field = args.get("field")
            if not isinstance(field, dict):
                return json.dumps({"ok": False, "error": "`field` (object) is required for add_field"})
            tbl = vault_datatable.add_field(path, field)
            return json.dumps({"ok": True, "table": tbl})

        if action == "remove_field":
            field_name = args.get("field_name", "")
            if not field_name:
                return json.dumps({"ok": False, "error": "`field_name` is required for remove_field"})
            tbl = vault_datatable.remove_field(path, field_name)
            return json.dumps({"ok": True, "table": tbl})

        if action == "rename_field":
            field_name = args.get("field_name", "")
            new_name = args.get("new_name", "")
            if not field_name or not new_name:
                return json.dumps({"ok": False, "error": "`field_name` and `new_name` are required for rename_field"})
            tbl = vault_datatable.rename_field(path, field_name, new_name)
            return json.dumps({"ok": True, "table": tbl})

        if action == "create_relation":
            field_name = args.get("field_name", "")
            target_table = args.get("target_table", "")
            cardinality = args.get("cardinality", "one")
            if not field_name or not target_table:
                return json.dumps({
                    "ok": False,
                    "error": "`field_name` and `target_table` are required for create_relation",
                })
            tbl = vault_datatable.create_relation(
                path, field_name, target_table, cardinality,
            )
            return json.dumps({"ok": True, "table": tbl})

        if action == "create_junction":
            table_a = args.get("table_a", "")
            table_b = args.get("table_b", "")
            if not table_a or not table_b:
                return json.dumps({
                    "ok": False,
                    "error": "`table_a` and `table_b` are required for create_junction",
                })
            tbl = vault_datatable.create_junction(
                path,
                table_a=table_a,
                table_b=table_b,
                title=args.get("title"),
            )
            return json.dumps({"ok": True, "path": path, "table": tbl})

        if action == "er_diagram":
            folder = args.get("folder", "")
            mermaid = vault_datatable_index.er_diagram(folder)
            return json.dumps({"ok": True, "folder": folder, "mermaid": mermaid})

        if action == "list_databases":
            dbs = vault_datatable_index.list_databases()
            return json.dumps({"ok": True, "databases": dbs, "count": len(dbs)})

        if action == "related_rows":
            row_id = args.get("row_id", "")
            if not row_id:
                return json.dumps({"ok": False, "error": "`row_id` is required for related_rows"})
            rel = vault_datatable.related_rows(path, row_id)
            return json.dumps({"ok": True, "path": path, "row_id": row_id, **rel})

        if action == "suggest_schema":
            # No-op stub: returns the user's description echoed back so the
            # calling skill can render it in chat. The skill itself walks the
            # user through proposed schemas; this action exists so the agent
            # can announce a structured "proposal" turn without mutating files.
            description = args.get("description", "")
            folder = args.get("folder", "")
            return json.dumps({
                "ok": True,
                "folder": folder,
                "description": description,
                "note": "Use `create_table` and `create_relation` to apply a proposal.",
            })

        return json.dumps({"ok": False, "error": f"unknown action: {action!r}"})

    except (KeyError, ValueError, FileNotFoundError, OSError) as exc:
        return json.dumps({"ok": False, "error": str(exc)})
