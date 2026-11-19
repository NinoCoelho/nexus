"""Nexus CLI — data-table subcommand group (SQL via DuckDB).

Thin wrappers over the ``datatable_manage`` agent-tool handler, so
external agents get the same structured-table surface as the in-Nexus
agent: DuckDB SQL queries, row CRUD, CSV import. Tables are vault
markdown (``data-table-plugin: basic``) — no daemon needed.
"""

from __future__ import annotations

import json

import typer

table_app = typer.Typer(help="Structured data tables (DuckDB SQL) in the vault", no_args_is_help=True)


def _invoke(args: dict) -> dict:
    from ..tools.datatable_tool import handle_datatable_tool

    raw = handle_datatable_tool(args)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "error": raw}


def _fail_if_error(res: dict) -> None:
    if not res.get("ok", True):
        typer.echo(f"error: {res.get('error', res)}", err=True)
        raise typer.Exit(1)


def _echo_payload(res: dict, json_out: bool, keys: tuple[str, ...] = ()) -> None:
    if json_out:
        typer.echo(json.dumps(res, ensure_ascii=False, indent=2))
        return
    if keys:
        for k in keys:
            if k in res:
                v = res[k]
                typer.echo(json.dumps(v, ensure_ascii=False, indent=2) if not isinstance(v, str) else v)
    else:
        for k, v in res.items():
            if k == "ok":
                continue
            typer.echo(f"{k}: {json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v}")


@table_app.command("list")
def list_tables(json_out: bool = typer.Option(False, "--json")) -> None:
    """List databases (folders) and their tables."""
    res = _invoke({"action": "list_databases"})
    _fail_if_error(res)
    _echo_payload(res, json_out)


@table_app.command("view")
def view(
    path: str = typer.Argument(..., help="Table vault path (e.g. data/orders.md)."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """View schema, types, relations and row count (metadata + 3 samples)."""
    res = _invoke({"action": "view", "path": path})
    _fail_if_error(res)
    _echo_payload(res, json_out)


@table_app.command("sql")
def sql(
    path: str = typer.Argument(..., help="Table vault path. Refer to it as `t` in the SQL."),
    query: str = typer.Argument(..., help="DuckDB SQL against `t` (e.g. \"SELECT status, COUNT(*) FROM t GROUP BY status\")."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Run DuckDB SQL against the table (aliased as `t`)."""
    res = _invoke({"action": "query", "path": path, "sql": query})
    _fail_if_error(res)
    _echo_payload(res, json_out, keys=("results", "summary", "columns", "rows"))


@table_app.command("rows")
def rows(
    path: str = typer.Argument(...),
    limit: int = typer.Option(25, "--limit", "-n"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """List rows (paginated; for CRUD checks, not analysis — use sql)."""
    res = _invoke({"action": "list_rows", "path": path, "limit": limit})
    _fail_if_error(res)
    _echo_payload(res, json_out, keys=("rows",))


@table_app.command("find")
def find(
    path: str = typer.Argument(...),
    q: str = typer.Argument(..., help="Substring looked up across fields."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Find rows by substring across fields."""
    res = _invoke({"action": "find_rows", "path": path, "q": q})
    _fail_if_error(res)
    _echo_payload(res, json_out, keys=("rows",))


@table_app.command("add")
def add(
    path: str = typer.Argument(...),
    row: str = typer.Argument(..., help='Row as JSON, e.g. \'{"title": "x", "status": "todo"}\'.'),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Add one row from a JSON object."""
    try:
        row_data = json.loads(row)
    except json.JSONDecodeError as exc:
        typer.echo(f"error: row is not valid JSON: {exc}", err=True)
        raise typer.Exit(2)
    res = _invoke({"action": "add_row", "path": path, "row": row_data})
    _fail_if_error(res)
    _echo_payload(res, json_out)


@table_app.command("update")
def update(
    path: str = typer.Argument(...),
    row_id: str = typer.Argument(..., help="Row id."),
    patch: str = typer.Argument(..., help='Fields to change as JSON, e.g. \'{"status": "done"}\'.'),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Update a row's fields from a JSON object."""
    try:
        patch_data = json.loads(patch)
    except json.JSONDecodeError as exc:
        typer.echo(f"error: patch is not valid JSON: {exc}", err=True)
        raise typer.Exit(2)
    res = _invoke({"action": "update_row", "path": path, "row_id": row_id, "row": patch_data})
    _fail_if_error(res)
    _echo_payload(res, json_out)


@table_app.command("rm")
def rm(
    path: str = typer.Argument(...),
    row_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete a row (irreversible)."""
    if not yes:
        typer.confirm(f"Delete row {row_id} from {path}?", abort=True)
    res = _invoke({"action": "delete_row", "path": path, "row_id": row_id})
    _fail_if_error(res)
    typer.echo(f"Deleted row {row_id}.")


@table_app.command("create")
def create(
    path: str = typer.Argument(..., help="New table vault path (e.g. data/orders.md)."),
    schema: str = typer.Argument(..., help='Schema JSON: {"fields": [{"name": "title", "type": "text"}, ...]}.'),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Create a table with a typed schema."""
    try:
        schema_data = json.loads(schema)
    except json.JSONDecodeError as exc:
        typer.echo(f"error: schema is not valid JSON: {exc}", err=True)
        raise typer.Exit(2)
    res = _invoke({"action": "create_table", "path": path, "schema": schema_data})
    _fail_if_error(res)
    typer.echo(f"Created table {path}.")


@table_app.command("import-csv")
def import_csv(
    path: str = typer.Argument(..., help="Target table path (created if missing)."),
    csv_path: str = typer.Argument(..., help="Local CSV file to ingest."),
    json_out: bool = typer.Option(False, "--json"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview first 5 mapped rows without writing."),
) -> None:
    """Bulk-import a local CSV file into a table (staged via vault tmp)."""
    import time as _time
    from pathlib import Path as _Path

    from .. import vault as _vault

    content = _Path(csv_path).expanduser().read_text(encoding="utf-8")
    staged = f".tool-cache/import-{int(_time.time())}.csv"
    _vault.write_file(staged, content)
    try:
        args: dict = {"action": "import_csv", "path": path, "source": staged}
        if dry_run:
            args["dry_run"] = True
        res = _invoke(args)
    finally:
        try:
            _vault.delete(staged)
        except Exception:
            pass
    _fail_if_error(res)
    _echo_payload(res, json_out)
