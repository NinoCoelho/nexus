"""Vault-native data table — markdown file type parallel to kanban.

Format
------
A data-table file has YAML frontmatter with ``data-table-plugin: basic`` and
a markdown body with two fenced YAML blocks:

    ---
    data-table-plugin: basic
    ---

    ## Schema
    ```yaml
    title: Bug triage
    fields:
      - { name: id, kind: text, required: true }
      - { name: severity, kind: select, choices: [low, med, high] }
    ```

    ## Rows
    ```yaml
    - { id: BUG-1, severity: high }
    ```

Human-readable, diff-friendly, survives hand edits.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import yaml

from . import vault

DATATABLE_PLUGIN_KEY = "data-table-plugin"

_FENCE_RE = re.compile(
    r"```ya?ml\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)
_SCHEMA_SECTION = re.compile(r"^##\s+Schema\s*$", re.MULTILINE | re.IGNORECASE)
_ROWS_SECTION = re.compile(r"^##\s+Rows\s*$", re.MULTILINE | re.IGNORECASE)
_VIEWS_SECTION = re.compile(r"^##\s+Views\s*$", re.MULTILINE | re.IGNORECASE)


def is_datatable_file(content: str) -> bool:
    """Return True if the file's frontmatter declares it a data-table."""
    if not content.startswith("---"):
        return False
    end = content.find("\n---", 3)
    if end == -1:
        return False
    try:
        fm = yaml.safe_load(content[3:end]) or {}
    except yaml.YAMLError:
        return False
    return isinstance(fm, dict) and DATATABLE_PLUGIN_KEY in fm


def _extract_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter_dict, body) or ({}, content)."""
    if not content.startswith("---"):
        return {}, content
    end = content.find("\n---", 3)
    if end == -1:
        return {}, content
    try:
        fm = yaml.safe_load(content[3:end]) or {}
        if not isinstance(fm, dict):
            fm = {}
    except yaml.YAMLError:
        fm = {}
    return fm, content[end + 4:].lstrip("\n")


def _extract_section_yaml(body: str, section_re: re.Pattern) -> tuple[Any, int, int]:
    """Find a section header and extract the first fenced YAML block after it.

    Returns (parsed_value, fence_start, fence_end) where fence_start/end are
    absolute positions in body. Returns (None, -1, -1) if not found.
    """
    m_sec = section_re.search(body)
    if not m_sec:
        return None, -1, -1
    after = body[m_sec.end():]
    m_fence = _FENCE_RE.search(after)
    if not m_fence:
        return None, -1, -1
    raw = m_fence.group(1)
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError:
        parsed = None
    fence_start = m_sec.end() + m_fence.start()
    fence_end = m_sec.end() + m_fence.end()
    return parsed, fence_start, fence_end


def read_table(path: str) -> dict[str, Any]:
    """Read a data-table file and return {'schema', 'rows', 'views'}."""
    file = vault.read_file(path)
    content = file["content"]
    _, body = _extract_frontmatter(content)

    schema_raw, _, _ = _extract_section_yaml(body, _SCHEMA_SECTION)
    rows_raw, _, _ = _extract_section_yaml(body, _ROWS_SECTION)
    views_raw, _, _ = _extract_section_yaml(body, _VIEWS_SECTION)

    schema: dict[str, Any] = {}
    if isinstance(schema_raw, dict):
        schema = schema_raw

    rows: list[dict[str, Any]] = []
    if isinstance(rows_raw, list):
        for r in rows_raw:
            if isinstance(r, dict):
                if "_id" not in r:
                    r = {"_id": uuid.uuid4().hex[:8], **r}
                rows.append(r)

    views: list[dict[str, Any]] = []
    if isinstance(views_raw, list):
        views = [v for v in views_raw if isinstance(v, dict)]

    return {"schema": schema, "rows": rows, "views": views}


def _serialize(
    frontmatter: dict[str, Any],
    schema: dict[str, Any],
    rows: list[dict[str, Any]],
    views: list[dict[str, Any]] | None = None,
) -> str:
    fm_text = yaml.dump(frontmatter, default_flow_style=False, sort_keys=False).rstrip()
    schema_yaml = yaml.dump(schema, default_flow_style=False, sort_keys=False, allow_unicode=True).rstrip()
    rows_yaml = yaml.dump(rows, default_flow_style=False, sort_keys=False, allow_unicode=True).rstrip()
    out = (
        f"---\n{fm_text}\n---\n\n"
        f"## Schema\n```yaml\n{schema_yaml}\n```\n\n"
        f"## Rows\n```yaml\n{rows_yaml}\n```\n"
    )
    if views:
        views_yaml = yaml.dump(views, default_flow_style=False, sort_keys=False, allow_unicode=True).rstrip()
        out += f"\n## Views\n```yaml\n{views_yaml}\n```\n"
    return out


def _load_state(path: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read frontmatter + table; return (frontmatter, table-dict)."""
    file = vault.read_file(path)
    fm, _ = _extract_frontmatter(file["content"])
    fm.setdefault(DATATABLE_PLUGIN_KEY, "basic")
    return fm, read_table(path)


def create_table(path: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Scaffold a new data-table file and return the table dict."""
    fm = {DATATABLE_PLUGIN_KEY: "basic"}
    vault.write_file(path, _serialize(fm, schema, [], []))
    return {"schema": schema, "rows": [], "views": []}


def set_schema(path: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Replace the schema block, preserving existing rows."""
    fm, tbl = _load_state(path)
    vault.write_file(path, _serialize(fm, schema, tbl["rows"], tbl.get("views", [])))
    return {"schema": schema, "rows": tbl["rows"], "views": tbl.get("views", [])}


def set_views(path: str, views: list[dict[str, Any]]) -> dict[str, Any]:
    """Replace the views block."""
    fm, tbl = _load_state(path)
    vault.write_file(path, _serialize(fm, tbl["schema"], tbl["rows"], views))
    return {"schema": tbl["schema"], "rows": tbl["rows"], "views": views}


def add_row(path: str, row: dict[str, Any]) -> dict[str, Any]:
    """Append a row (auto-assigning _id if absent) and return the row."""
    fm, tbl = _load_state(path)
    if "_id" not in row:
        row = {"_id": uuid.uuid4().hex[:8], **row}
    tbl["rows"].append(row)
    vault.write_file(path, _serialize(fm, tbl["schema"], tbl["rows"], tbl.get("views", [])))
    return row


def add_rows(path: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Append multiple rows in one write (CSV import). Returns new rows."""
    fm, tbl = _load_state(path)
    added: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if "_id" not in r:
            r = {"_id": uuid.uuid4().hex[:8], **r}
        tbl["rows"].append(r)
        added.append(r)
    vault.write_file(path, _serialize(fm, tbl["schema"], tbl["rows"], tbl.get("views", [])))
    return added


def update_row(path: str, row_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    """Update fields of an existing row identified by _id."""
    fm, tbl = _load_state(path)
    for row in tbl["rows"]:
        if str(row.get("_id")) == str(row_id):
            row.update({k: v for k, v in updates.items() if k != "_id"})
            vault.write_file(path, _serialize(fm, tbl["schema"], tbl["rows"], tbl.get("views", [])))
            return row
    raise KeyError(f"row {row_id!r} not found")


def delete_row(path: str, row_id: str) -> None:
    """Delete a row by _id."""
    fm, tbl = _load_state(path)
    before = len(tbl["rows"])
    tbl["rows"] = [r for r in tbl["rows"] if str(r.get("_id")) != str(row_id)]
    if len(tbl["rows"]) == before:
        raise KeyError(f"row {row_id!r} not found")
    vault.write_file(path, _serialize(fm, tbl["schema"], tbl["rows"], tbl.get("views", [])))
