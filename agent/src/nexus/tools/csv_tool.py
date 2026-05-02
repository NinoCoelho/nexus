"""Agent tool: ``vault_csv`` — DuckDB-backed analytics over CSV files.

Single tool with an ``action`` discriminator so the LLM picks the operation
(``schema``, ``sample``, ``describe``, ``query``, ``relationships``).
"""

from __future__ import annotations

import json
from typing import Any

from ..agent.llm import ToolSpec

CSV_TOOL = ToolSpec(
    name="vault_csv",
    description=(
        "Analyze CSV/TSV files in the vault using DuckDB **without loading the "
        "data into the conversation context**. Prefer this over `vault_read` for "
        "any `.csv` / `.tsv` file.\n\n"
        "Actions:\n"
        "- `schema`: list columns, types, row_count and file_size.\n"
        "- `sample`: return N rows (`mode`: head/tail/random, default head, n=20).\n"
        "- `describe`: per-column stats — count, null_count, distinct_count, "
        "min/max/mean/stddev for numeric columns, top_values for categorical. "
        "Pass `columns` to limit; omit for all.\n"
        "- `query`: run a SQL SELECT against the CSV (the file is exposed as the "
        "view `t`). Only SELECT/WITH allowed. Result truncated to `limit` rows "
        "(default 200, max 1000). Response shape is columnar by default "
        "(`{columns: [...], data: [[...], ...]}`) — ~40% fewer tokens than the "
        "row-of-objects shape. Pass `format: \"rows\"` for the legacy shape "
        "(`{rows: [{col: val, ...}, ...]}`). Use `query` for groupby/aggregate/filter.\n"
        "- `relationships`: discover likely joins/FKs against other CSVs in the "
        "vault. Returns column pairs scored by name similarity + value overlap. "
        "Pass `candidates` to restrict to specific paths."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["schema", "sample", "describe", "query", "relationships"],
            },
            "path": {
                "type": "string",
                "description": "Vault-relative path to a `.csv` or `.tsv` file.",
            },
            "mode": {
                "type": "string",
                "enum": ["head", "tail", "random"],
                "description": "Sampling mode (action=sample).",
            },
            "n": {
                "type": "integer",
                "description": "Sample size (action=sample, default 20, max 1000).",
            },
            "columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Columns to describe (action=describe, default all).",
            },
            "sql": {
                "type": "string",
                "description": "SELECT statement (action=query). The CSV is the view `t`.",
            },
            "limit": {
                "type": "integer",
                "description": "Row cap for query results (default 200, max 1000).",
            },
            "format": {
                "type": "string",
                "enum": ["columns", "rows"],
                "description": "Response shape for action=query. 'columns' (default) returns {columns, data: [[...]]} — ~40% fewer tokens. 'rows' returns the legacy {rows: [{col: val}, ...]}.",
            },
            "candidates": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Other CSV paths to consider (action=relationships).",
            },
        },
        "required": ["action", "path"],
    },
)


def handle_csv_tool(args: dict[str, Any]) -> str:
    from .. import vault, vault_csv, vault_datatable

    def _dumps(obj: dict) -> str:
        return json.dumps(obj, default=str)

    try:
        action = args.get("action")
        path = args.get("path", "")
        if not action:
            return _dumps({"ok": False, "error": "`action` is required"})
        if not path:
            return _dumps({"ok": False, "error": "`path` is required"})

        # Redirect datatable .md files to datatable_manage. The agent has been
        # observed calling vault_csv on `data-table-plugin: basic` files and
        # giving up on the resulting "not a CSV/TSV file" error — give it a
        # structured next step instead of a flat failure.
        if path.lower().endswith(".md"):
            try:
                file = vault.read_file(path)
            except (FileNotFoundError, OSError):
                file = None
            if file is not None and vault_datatable.is_datatable_file(file["content"]):
                return _dumps({
                    "ok": False,
                    "error": f"`{path}` is a datatable, not a CSV — use `datatable_manage` instead",
                    "hint": {
                        "tool": "datatable_manage",
                        "suggested_actions": ["view", "list_rows", "add_row"],
                        "path": path,
                    },
                })

        if action == "schema":
            return _dumps({"ok": True, **vault_csv.csv_schema(path)})
        if action == "sample":
            mode = args.get("mode", "head")
            n = int(args.get("n", 20))
            return _dumps({"ok": True, **vault_csv.csv_sample(path, mode=mode, n=n)})
        if action == "describe":
            cols = args.get("columns")
            return _dumps({"ok": True, **vault_csv.csv_describe(path, columns=cols)})
        if action == "query":
            sql = args.get("sql", "")
            if not sql:
                return _dumps({"ok": False, "error": "`sql` is required for action=query"})
            limit = int(args.get("limit", 200))
            fmt = args.get("format", "columns")
            return _dumps({"ok": True, **vault_csv.csv_query(path, sql, limit=limit, fmt=fmt)})
        if action == "relationships":
            cands = args.get("candidates")
            return _dumps({"ok": True, **vault_csv.csv_relationships(path, candidates=cands)})

        return _dumps({"ok": False, "error": f"unknown action: {action!r}"})
    except (ValueError, FileNotFoundError, OSError) as exc:
        return _dumps({"ok": False, "error": str(exc)})
