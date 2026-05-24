from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from . import vault, vault_datatable

_KIND_TO_DUCKDB = {
    "number": "DOUBLE",
    "boolean": "BOOLEAN",
    "date": "DATE",
}


def _coerce_for_duckdb(value: Any, kind: str) -> Any:
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


def _derive_table_name(filename: str) -> str:
    name = filename
    if name.lower().endswith(".md"):
        name = name[:-3]
    name = name.replace("-", "_")
    name = re.sub(r"[^a-zA-Z0-9_]", "", name)
    if not name:
        name = "t"
    if name[0].isdigit():
        name = f"t_{name}"
    return name


def _resolve_table_path(filename: str, folder: str) -> str:
    filename = filename.replace("\\", "/")
    if filename.startswith("./"):
        filename = filename[2:]
    parts = [p for p in filename.split("/") if p and p != ".."]
    if parts and folder:
        prefix = folder.rstrip("/")
        return f"{prefix}/{'/'.join(parts)}"
    return "/".join(parts) if parts else filename


@dataclass
class TableDef:
    sql_name: str
    vault_path: str
    schema: dict[str, Any]
    rows: list[dict[str, Any]]


def _load_folder_tables(
    folder: str, query_tables: list[str] | None = None,
) -> list[TableDef]:
    folder_prefix = folder.rstrip("/") + "/" if folder else ""
    all_entries = vault.list_tree()

    file_entries = [
        e for e in all_entries
        if e.type == "file"
        and e.path.endswith(".md")
        and e.path.startswith(folder_prefix)
    ]

    if query_tables is not None:
        resolved = set()
        for qt in query_tables:
            rp = _resolve_table_path(qt, folder)
            if rp.startswith(folder_prefix):
                resolved.add(rp)
            else:
                resolved.add(qt)
        file_entries = [e for e in file_entries if e.path in resolved]

    tables: list[TableDef] = []
    for entry in file_entries:
        try:
            file_data = vault.read_file(entry.path)
        except (FileNotFoundError, OSError):
            continue
        if not vault_datatable.is_datatable_file(file_data["content"]):
            continue
        try:
            tbl = vault_datatable.read_table(entry.path)
        except Exception:
            continue
        rel_name = entry.path
        if folder_prefix and rel_name.startswith(folder_prefix):
            rel_name = rel_name[len(folder_prefix):]
        sql_name = _derive_table_name(rel_name)
        tables.append(TableDef(
            sql_name=sql_name,
            vault_path=entry.path,
            schema=tbl.get("schema") or {},
            rows=tbl.get("rows") or [],
        ))
    return tables


def _materialize_tables(con: Any, tables: list[TableDef]) -> None:
    for tdef in tables:
        fields = tdef.schema.get("fields") or []
        field_specs = [
            (f["name"], f.get("kind", "text"))
            for f in fields
            if isinstance(f, dict) and f.get("name")
        ]
        if not field_specs:
            con.execute(f'CREATE TABLE {_quote_ident(tdef.sql_name)} (_empty VARCHAR)')
            continue
        cols_def = ", ".join(
            f"{_quote_ident(name)} {_KIND_TO_DUCKDB.get(kind, 'VARCHAR')}"
            for name, kind in field_specs
        )
        con.execute(f"CREATE TABLE {_quote_ident(tdef.sql_name)} ({cols_def})")
        if not tdef.rows:
            continue
        placeholders = ", ".join("?" * len(field_specs))
        col_list = ", ".join(_quote_ident(n) for n, _ in field_specs)
        tuples = [
            tuple(_coerce_for_duckdb(r.get(n), k) for n, k in field_specs)
            for r in tdef.rows
        ]
        con.executemany(
            f"INSERT INTO {_quote_ident(tdef.sql_name)} ({col_list}) VALUES ({placeholders})",
            tuples,
        )


_SQL_LEAD_RE = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)


def execute_widget_query(
    folder: str,
    sql: str,
    query_tables: list[str] | None = None,
) -> dict[str, Any]:
    import duckdb

    if not isinstance(sql, str) or not sql.strip():
        return {"error": "sql is required", "columns": [], "rows": [], "row_count": 0}
    if not _SQL_LEAD_RE.match(sql):
        return {"error": "only SELECT / WITH queries are allowed", "columns": [], "rows": [], "row_count": 0}

    try:
        tables = _load_folder_tables(folder, query_tables)
    except Exception as exc:
        return {"error": str(exc), "columns": [], "rows": [], "row_count": 0}

    if not tables:
        return {"error": f"no data-tables found in folder {folder!r}", "columns": [], "rows": [], "row_count": 0}

    limit = 500
    con = duckdb.connect(database=":memory:")
    try:
        _materialize_tables(con, tables)
        wrapped = f"SELECT * FROM ({sql}) AS __wq LIMIT {limit + 1}"
        rel = con.execute(wrapped)
        col_names = [d[0] for d in rel.description]
        col_meta = [{"name": c, "type": str(t)} for c, t in zip(col_names, [d[1] for d in rel.description])]
        raw_rows = rel.fetchall()
        truncated = len(raw_rows) > limit
        result_rows = raw_rows[:limit]
        row_dicts = [dict(zip(col_names, r)) for r in result_rows]
        return {
            "columns": col_meta,
            "rows": row_dicts,
            "row_count": len(result_rows),
            "truncated": truncated,
        }
    except Exception as exc:
        return {"error": str(exc), "columns": [], "rows": [], "row_count": 0}
    finally:
        con.close()
