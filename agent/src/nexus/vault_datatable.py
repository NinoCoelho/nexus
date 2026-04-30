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

import posixpath
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


# ── Typed relations (kind: ref) ──────────────────────────────────────────────


def resolve_ref(host_path: str, target_table: str) -> str:
    """Resolve a `target_table` from a schema field into a vault-relative path.

    Schemas may declare `target_table` either as a vault-absolute path
    (``data/customers.md``) or relative to the host file (``../people/people.md``).
    Returned paths use forward slashes and are normalized — they do NOT escape
    the vault root (caller is responsible for that via vault.resolve_path).
    """
    if not target_table:
        return ""
    t = target_table.strip()
    # Absolute-style targets stay as-is.
    if not t.startswith(".") and not t.startswith("/"):
        return posixpath.normpath(t)
    # Relative — anchor at host's directory.
    host_dir = posixpath.dirname(host_path or "")
    joined = posixpath.normpath(posixpath.join(host_dir, t.lstrip("/")))
    # normpath may produce ".." prefixes if relative path escapes; pass through
    # so vault._safe_resolve raises later instead of silently mis-resolving.
    return joined


def _ref_fields(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Return fields with kind == 'ref'."""
    fields = schema.get("fields") if isinstance(schema, dict) else None
    if not isinstance(fields, list):
        return []
    return [f for f in fields if isinstance(f, dict) and f.get("kind") == "ref"]


def is_junction(schema: dict[str, Any]) -> bool:
    """Heuristic: is this schema a junction (N:N) table?

    A junction has exactly two `kind: ref` fields and no other content fields
    (the auto-assigned `_id` doesn't count). Explicit override via
    `table.is_junction: true|false` always wins.
    """
    if not isinstance(schema, dict):
        return False
    table_meta = schema.get("table")
    if isinstance(table_meta, dict) and "is_junction" in table_meta:
        return bool(table_meta["is_junction"])
    refs = _ref_fields(schema)
    if len(refs) != 2:
        return False
    fields = schema.get("fields", [])
    non_ref = [
        f for f in fields
        if isinstance(f, dict)
        and f.get("kind") != "ref"
        and f.get("name") not in (None, "", "_id")
    ]
    return len(non_ref) == 0


def validate_schema(schema: dict[str, Any], host_path: str = "") -> list[str]:
    """Return a list of warning strings for the schema (never raises).

    Currently checks:
      * Each `kind: ref` field has a non-empty `target_table`.
      * `cardinality` (when present) is one of "one"|"many".
    """
    warnings: list[str] = []
    for f in _ref_fields(schema):
        name = f.get("name") or "(unnamed)"
        target = f.get("target_table")
        if not target or not isinstance(target, str) or not target.strip():
            warnings.append(f"field {name!r}: kind=ref needs a target_table")
        card = f.get("cardinality")
        if card is not None and card not in ("one", "many"):
            warnings.append(
                f"field {name!r}: cardinality={card!r} should be 'one' or 'many'"
            )
    return warnings


def add_field(path: str, field: dict[str, Any]) -> dict[str, Any]:
    """Append a field to the schema, preserving rows."""
    if not isinstance(field, dict) or not field.get("name"):
        raise ValueError("field must be a dict with a `name`")
    fm, tbl = _load_state(path)
    schema = tbl["schema"] or {}
    fields = list(schema.get("fields", []))
    if any(isinstance(f, dict) and f.get("name") == field["name"] for f in fields):
        raise ValueError(f"field {field['name']!r} already exists")
    fields.append(field)
    schema["fields"] = fields
    vault.write_file(path, _serialize(fm, schema, tbl["rows"], tbl.get("views", [])))
    return {"schema": schema, "rows": tbl["rows"], "views": tbl.get("views", [])}


def remove_field(path: str, field_name: str) -> dict[str, Any]:
    """Drop a field from the schema. Existing row values for that field stay
    on disk (orphaned) — same trade-off as set_schema."""
    fm, tbl = _load_state(path)
    schema = tbl["schema"] or {}
    fields = [
        f for f in schema.get("fields", [])
        if not (isinstance(f, dict) and f.get("name") == field_name)
    ]
    schema["fields"] = fields
    vault.write_file(path, _serialize(fm, schema, tbl["rows"], tbl.get("views", [])))
    return {"schema": schema, "rows": tbl["rows"], "views": tbl.get("views", [])}


def rename_field(path: str, old_name: str, new_name: str) -> dict[str, Any]:
    """Rename a field and migrate existing row values to the new key."""
    if not new_name:
        raise ValueError("new_name must be non-empty")
    fm, tbl = _load_state(path)
    schema = tbl["schema"] or {}
    found = False
    for f in schema.get("fields", []):
        if isinstance(f, dict) and f.get("name") == old_name:
            f["name"] = new_name
            found = True
            break
    if not found:
        raise KeyError(f"field {old_name!r} not found")
    rows = tbl["rows"]
    for r in rows:
        if old_name in r:
            r[new_name] = r.pop(old_name)
    vault.write_file(path, _serialize(fm, schema, rows, tbl.get("views", [])))
    return {"schema": schema, "rows": rows, "views": tbl.get("views", [])}


def create_relation(
    from_table: str,
    field_name: str,
    target_table: str,
    cardinality: str = "one",
    *,
    label: str | None = None,
) -> dict[str, Any]:
    """Add a `kind: ref` field on ``from_table`` pointing at ``target_table``."""
    if cardinality not in ("one", "many"):
        raise ValueError("cardinality must be 'one' or 'many'")
    field: dict[str, Any] = {
        "name": field_name,
        "kind": "ref",
        "target_table": target_table,
        "cardinality": cardinality,
    }
    if label:
        field["label"] = label
    return add_field(from_table, field)


def create_junction(
    path: str,
    *,
    table_a: str,
    table_b: str,
    field_a: str | None = None,
    field_b: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Scaffold a junction table linking ``table_a`` and ``table_b``.

    The two ref fields default to the basename of each target (without ``.md``)
    suffixed with ``_id``; pass ``field_a`` / ``field_b`` to override. The
    junction is auto-detected as N:N because it has exactly two refs and no
    other content fields.
    """
    def _basename(p: str) -> str:
        return p.rsplit("/", 1)[-1].removesuffix(".md")

    a = field_a or f"{_basename(table_a)}_id"
    b = field_b or f"{_basename(table_b)}_id"
    if a == b:
        b = b + "_2"
    schema: dict[str, Any] = {
        "fields": [
            {"name": a, "kind": "ref", "target_table": table_a, "cardinality": "one"},
            {"name": b, "kind": "ref", "target_table": table_b, "cardinality": "one"},
        ],
    }
    if title:
        schema["title"] = title
    return create_table(path, schema)


def find_inbound_refs(table_path: str) -> list[dict[str, Any]]:
    """Find every (table, field) in the vault whose ref targets ``table_path``.

    Returns dicts of:
      ``{from_table, from_title, field_name, cardinality, is_junction, other_ref}``
    where ``other_ref`` is the second ref field in a junction table (None
    otherwise). Walks the vault tree once; caller may cache.
    """
    results: list[dict[str, Any]] = []
    for entry in vault.list_tree():
        if entry.type != "file" or not entry.path.endswith(".md"):
            continue
        try:
            file = vault.read_file(entry.path)
        except (FileNotFoundError, OSError):
            continue
        if not is_datatable_file(file["content"]):
            continue
        try:
            tbl = read_table(entry.path)
        except Exception:
            continue
        schema = tbl["schema"]
        refs = _ref_fields(schema)
        if not refs:
            continue
        host_junction = is_junction(schema)
        for ref in refs:
            target = ref.get("target_table") or ""
            if resolve_ref(entry.path, target) != table_path:
                continue
            other_ref: dict[str, Any] | None = None
            if host_junction:
                other_ref = next(
                    (r for r in refs if r is not ref and r.get("name")),
                    None,
                )
            title = (
                schema.get("title")
                if isinstance(schema, dict) and isinstance(schema.get("title"), str)
                else None
            )
            if not title:
                title = entry.path.rsplit("/", 1)[-1].removesuffix(".md")
            results.append({
                "from_table": entry.path,
                "from_title": title,
                "field_name": ref.get("name", ""),
                "cardinality": ref.get("cardinality", "one"),
                "is_junction": host_junction,
                "other_ref": (
                    {
                        "field_name": other_ref.get("name", ""),
                        "target_table": resolve_ref(
                            entry.path, other_ref.get("target_table", "") or "",
                        ),
                    }
                    if other_ref else None
                ),
            })
    return results


def _row_matches_ref(row: dict[str, Any], field_name: str, row_id: str) -> bool:
    """True if ``row[field_name]`` references ``row_id`` (single value or array)."""
    val = row.get(field_name)
    if val is None:
        return False
    if isinstance(val, list):
        return any(str(v) == row_id for v in val)
    return str(val) == row_id


def related_rows(table_path: str, row_id: str) -> dict[str, Any]:
    """Find rows in other tables that reference ``(table_path, row_id)``.

    Returns:
        ``{
            "one_to_many": [{from_table, from_title, field_name, rows: [...]}],
            "many_to_many": [{junction_table, junction_title, target_table,
                              target_title, rows: [...]}]
          }``

    Junction tables (auto or explicit) are collapsed: instead of surfacing the
    junction rows themselves, we resolve through them to the rows on the other
    side. Non-junction inbound refs (any cardinality) appear under one_to_many.
    """
    one_to_many: list[dict[str, Any]] = []
    many_to_many: list[dict[str, Any]] = []
    for ref in find_inbound_refs(table_path):
        try:
            from_tbl = read_table(ref["from_table"])
        except Exception:
            continue
        matches = [
            r for r in from_tbl["rows"]
            if _row_matches_ref(r, ref["field_name"], row_id)
        ]
        if ref["is_junction"] and ref["other_ref"]:
            other = ref["other_ref"]
            target = other["target_table"]
            if not target:
                continue
            try:
                target_tbl = read_table(target)
            except Exception:
                continue
            target_meta = target_tbl["schema"].get("table") if isinstance(target_tbl["schema"], dict) else None
            target_pk = (
                target_meta.get("primary_key")
                if isinstance(target_meta, dict) and target_meta.get("primary_key")
                else "_id"
            )
            other_field = other["field_name"]
            wanted_ids: set[str] = set()
            for jrow in matches:
                v = jrow.get(other_field)
                if isinstance(v, list):
                    for x in v:
                        wanted_ids.add(str(x))
                elif v is not None:
                    wanted_ids.add(str(v))
            target_rows = [
                r for r in target_tbl["rows"]
                if str(r.get(target_pk, r.get("_id", ""))) in wanted_ids
            ]
            target_title = (
                target_tbl["schema"].get("title")
                if isinstance(target_tbl["schema"], dict)
                and isinstance(target_tbl["schema"].get("title"), str)
                else target.rsplit("/", 1)[-1].removesuffix(".md")
            )
            many_to_many.append({
                "junction_table": ref["from_table"],
                "junction_title": ref["from_title"],
                "target_table": target,
                "target_title": target_title,
                "rows": target_rows,
                "count": len(target_rows),
            })
        else:
            one_to_many.append({
                "from_table": ref["from_table"],
                "from_title": ref["from_title"],
                "field_name": ref["field_name"],
                "cardinality": ref["cardinality"],
                "rows": matches,
                "count": len(matches),
            })
    return {"one_to_many": one_to_many, "many_to_many": many_to_many}


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
