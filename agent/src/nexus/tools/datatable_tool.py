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
        "find_rows (look up by exact `where: {field: value}` and/or "
        "case-insensitive substring `q` across text fields — use this for "
        "'show me X' / 'find Y' queries; never claim 'not found' from a "
        "single `list_rows` page), "
        "query (run DuckDB SQL against the table — referenced as `t` — for "
        "aggregations, joins via subqueries, filters that find_rows can't "
        "express; SELECT/WITH only, capped at 1000 rows, returns "
        "`columns`/`data` like vault_csv), "
        "set_schema, set_views, add_field, remove_field, rename_field, "
        "create_relation, create_junction, suggest_schema, er_diagram, "
        "list_databases, related_rows, "
        "import_csv (one-shot bulk ingest: reads a vault `source` CSV/TSV, applies "
        "optional `mapping` of source-col → target-field, casts to target field "
        "kinds, calls add_rows. Set `dry_run: true` to preview the first 5 mapped "
        "rows without writing — recommended before committing a large import)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "create_table", "view",
                    "add_row", "add_rows", "update_row", "delete_row",
                    "list_rows", "find_rows", "query",
                    "set_schema", "set_views",
                    "add_field", "remove_field", "rename_field",
                    "create_relation", "create_junction",
                    "suggest_schema", "er_diagram",
                    "list_databases", "related_rows",
                    "import_csv",
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
                "description": "Page size for list_rows / find_rows (default 100, max 1000).",
            },
            "offset": {
                "type": "integer",
                "description": "Page offset for list_rows / find_rows (default 0).",
            },
            "where": {
                "type": "object",
                "description": (
                    "find_rows: exact-match filter `{field: value}`. Combined "
                    "with `q` via AND. List-valued cells use membership."
                ),
            },
            "q": {
                "type": "string",
                "description": (
                    "find_rows: case-insensitive substring matched against "
                    "every text/textarea field plus `_id`. Use this for "
                    "'show me John Doe' / 'find the order with SKU…'. "
                    "Combined with `where` via AND."
                ),
            },
            "views": {
                "type": "array",
                "description": (
                    "List of saved view presets for set_views. "
                    "Each view: { name: str, filter?: str, sort?: {field, dir: 'asc'|'desc'}, hidden?: [field_names] }"
                ),
                "items": {"type": "object"},
            },
            "source": {
                "type": "string",
                "description": (
                    "Vault-relative path to a `.csv` / `.tsv` file (action=import_csv). "
                    "Read with the standard csv module — auto-detects ',' / '\\t' / ';'."
                ),
            },
            "mapping": {
                "type": "object",
                "description": (
                    "Column mapping for action=import_csv: { source_col: target_field }. "
                    "Omitted keys default to identity (source col name = target field name). "
                    "Set a target to null to drop a source column."
                ),
                "additionalProperties": {"type": ["string", "null"]},
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "action=import_csv: when true, return the first 5 mapped rows + row count "
                    "without writing. Use this to verify the column mapping before committing."
                ),
            },
            "sql": {
                "type": "string",
                "description": (
                    "DuckDB SQL for action=query. Reference the table as `t` "
                    "(same convention as vault_csv). SELECT / WITH only — DDL "
                    "and writes are rejected. Limit defaults to 200 rows, hard "
                    "cap 1000; truncated results carry `truncated: true`."
                ),
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
        return json.dumps({"ok": False, "error": f"`path` is required for action '{action}' (use `folder` for er_diagram/suggest_schema/list_databases)"})

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

        if action == "find_rows":
            where = args.get("where") or {}
            q = args.get("q")
            if not isinstance(where, dict):
                return json.dumps({"ok": False, "error": "`where` must be an object"})
            offset = max(0, int(args.get("offset", 0)))
            limit_raw = args.get("limit")
            limit = 100 if limit_raw is None else max(1, min(int(limit_raw), 1000))
            result = vault_datatable.find_rows(
                path,
                where=where if where else None,
                q=q if isinstance(q, str) else None,
                limit=limit,
                offset=offset,
            )
            return json.dumps({"ok": True, **result})

        if action == "query":
            sql = args.get("sql")
            if not isinstance(sql, str) or not sql.strip():
                return json.dumps({"ok": False, "error": "`sql` is required for query"})
            limit_raw = args.get("limit")
            try:
                limit = int(limit_raw) if limit_raw is not None else 200
            except (TypeError, ValueError):
                return json.dumps({"ok": False, "error": "`limit` must be an integer"})
            tbl = vault_datatable.read_table(path)
            payload = _run_datatable_query(tbl, sql, limit=limit)
            return json.dumps({"ok": True, "path": path, **payload})

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
            required = _required_fields(vault_datatable, path)
            report = vault_datatable.add_rows_with_report(
                path, rows, required_fields=required,
            )
            response: dict[str, Any] = {
                "ok": True,
                "added": report["added"],
                "count": len(report["added"]),
            }
            if report["skipped"]:
                response["skipped"] = report["skipped"]
                response["skipped_count"] = len(report["skipped"])
            return json.dumps(response)

        if action == "import_csv":
            source = args.get("source", "")
            if not source:
                return json.dumps({"ok": False, "error": "`source` is required for import_csv"})
            mapping = args.get("mapping") or {}
            if not isinstance(mapping, dict):
                return json.dumps({"ok": False, "error": "`mapping` must be an object"})
            dry_run = bool(args.get("dry_run", False))
            mapped, total, schema_fields = _import_csv_to_rows(source, path, mapping)
            if dry_run:
                return json.dumps({
                    "ok": True,
                    "dry_run": True,
                    "total": total,
                    "preview": mapped[:5],
                    "target_fields": schema_fields,
                })
            required = _required_fields(vault_datatable, path)
            report = vault_datatable.add_rows_with_report(
                path, mapped, required_fields=required,
            )
            response = {
                "ok": True,
                "added_count": len(report["added"]),
                "total": total,
            }
            if report["skipped"]:
                response["skipped"] = report["skipped"]
                response["skipped_count"] = len(report["skipped"])
            return json.dumps(response)

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


_KIND_TO_DUCKDB = {
    "number": "DOUBLE",
    "boolean": "BOOLEAN",
    "date": "DATE",
}


def _coerce_for_duckdb(value: Any, kind: str) -> Any:
    """Convert a row cell into a DuckDB-friendly Python scalar.

    Lists / dicts are JSON-encoded so the agent can still grep them with
    ``LIKE`` or ``json_extract``. Numbers and booleans are best-effort cast;
    bad casts become ``NULL`` (rather than raising) to keep the query running.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    if kind == "number":
        if isinstance(value, bool):
            return float(value)
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("true", "1", "yes", "y")
    if kind == "date":
        return str(value)
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _run_datatable_query(
    tbl: dict[str, Any],
    sql: str,
    *,
    limit: int = 200,
) -> dict[str, Any]:
    """Materialize ``tbl`` into an in-memory DuckDB table ``t`` and run ``sql``.

    Reuses :func:`nexus.vault_csv.run_select` for the SELECT-only guard,
    LIMIT/truncation handling, and the response envelope so the shape matches
    ``vault_csv``'s ``query`` action.
    """
    import duckdb

    from .. import vault_csv

    fields = (tbl.get("schema") or {}).get("fields") or []
    field_specs = [
        (f["name"], f.get("kind", "text"))
        for f in fields
        if isinstance(f, dict) and f.get("name")
    ]
    rows = tbl.get("rows") or []

    con = duckdb.connect(database=":memory:")
    try:
        if not field_specs:
            con.execute('CREATE TABLE t (_empty VARCHAR)')
        else:
            cols_def = ", ".join(
                f"{_quote_ident(name)} {_KIND_TO_DUCKDB.get(kind, 'VARCHAR')}"
                for name, kind in field_specs
            )
            con.execute(f"CREATE TABLE t ({cols_def})")
            if rows:
                placeholders = ", ".join("?" * len(field_specs))
                col_list = ", ".join(_quote_ident(n) for n, _ in field_specs)
                tuples = [
                    tuple(_coerce_for_duckdb(r.get(n), k) for n, k in field_specs)
                    for r in rows
                ]
                con.executemany(
                    f"INSERT INTO t ({col_list}) VALUES ({placeholders})",
                    tuples,
                )
        return vault_csv.run_select(con, sql, limit=limit)
    finally:
        con.close()


def _required_fields(vault_datatable_mod, path: str) -> list[str]:
    """Return the names of fields marked ``required: true`` on the target table.

    Returns an empty list if the table cannot be read or the schema is empty —
    callers treat the absence of required fields as "no required-field check".
    """
    try:
        target = vault_datatable_mod.read_table(path)
    except (FileNotFoundError, OSError, ValueError):
        return []
    fields = (target.get("schema") or {}).get("fields") or []
    return [
        f.get("name") for f in fields
        if isinstance(f, dict) and f.get("required") and f.get("name")
    ]


def _import_csv_to_rows(
    source: str,
    target_path: str,
    mapping: dict[str, Any],
) -> tuple[list[dict[str, Any]], int, list[str]]:
    """Read a vault CSV/TSV and produce rows matching the target table's schema.

    Returns ``(mapped_rows, total_rows_read, target_field_names)``. Source
    columns are mapped to target fields using ``mapping`` (defaulting to
    identity). Values are cast to the target field ``kind`` (number, boolean,
    date) when a clean cast is possible; otherwise the raw string passes
    through. Unknown source columns and columns mapped to ``null`` are
    dropped silently.
    """
    import csv as _csv

    from .. import vault, vault_datatable

    target = vault_datatable.read_table(target_path)
    fields = target.get("schema", {}).get("fields", []) or []
    field_kinds = {f.get("name"): f.get("kind", "text") for f in fields if f.get("name")}
    target_field_names = list(field_kinds.keys())

    src_abs = vault.resolve_path(source)
    if not src_abs.is_file():
        raise FileNotFoundError(f"source not found: {source}")

    text = src_abs.read_text(encoding="utf-8", errors="replace")
    sniffer = _csv.Sniffer()
    sample = text[:4096]
    try:
        dialect = sniffer.sniff(sample, delimiters=",\t;|")
    except _csv.Error:
        dialect = _csv.excel  # comma fallback

    reader = _csv.DictReader(io_text(text), dialect=dialect)
    src_cols = list(reader.fieldnames or [])

    def _resolve_target(src_col: str) -> str | None:
        if src_col in mapping:
            return mapping[src_col]  # may be None → drop
        return src_col if src_col in field_kinds else None

    mapped: list[dict[str, Any]] = []
    total = 0
    for raw in reader:
        total += 1
        row: dict[str, Any] = {}
        for src_col in src_cols:
            tgt = _resolve_target(src_col)
            if not tgt:
                continue
            value = raw.get(src_col)
            row[tgt] = _cast_value(value, field_kinds.get(tgt, "text"))
        mapped.append(row)

    return mapped, total, target_field_names


def io_text(s: str):
    import io as _io
    return _io.StringIO(s)


def _cast_value(raw: Any, kind: str) -> Any:
    """Cast a raw CSV string to the target field kind. Lossy casts return raw."""
    if raw is None or raw == "":
        return None
    s = str(raw).strip()
    if not s:
        return None
    if kind == "number":
        try:
            f = float(s)
            return int(f) if f.is_integer() else f
        except ValueError:
            return s
    if kind == "boolean":
        low = s.lower()
        if low in ("true", "yes", "y", "1", "t"):
            return True
        if low in ("false", "no", "n", "0", "f"):
            return False
        return s
    return s
