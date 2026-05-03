"""Discovery layer for vault data-tables grouped into "databases."

A folder containing ≥1 file with ``data-table-plugin: basic`` frontmatter is
treated as a database. The walk is one-shot and not cached: at typical vault
sizes (≤10k files) the cost is ~50 ms; profile and add a SQLite-backed index
only if it overshoots.
"""

from __future__ import annotations

import posixpath
from typing import Any

from . import vault, vault_datatable


def _table_title(schema: dict[str, Any], path: str) -> str:
    """Title from schema → filename stem fallback."""
    title = schema.get("title") if isinstance(schema, dict) else None
    if isinstance(title, str) and title.strip():
        return title.strip()
    stem = path.rsplit("/", 1)[-1].removesuffix(".md")
    return stem or path


def _database_title(folder: str) -> str:
    if not folder:
        return "(root)"
    return folder.rsplit("/", 1)[-1] or folder


def _walk_tables() -> list[dict[str, Any]]:
    """Return [{path, folder, title, row_count}] for every data-table file."""
    out: list[dict[str, Any]] = []
    for entry in vault.list_tree():
        if entry.type != "file":
            continue
        path = entry.path
        if not path.endswith(".md"):
            continue
        try:
            file = vault.read_file(path)
        except (FileNotFoundError, OSError):
            continue
        if not vault_datatable.is_datatable_file(file["content"]):
            continue
        try:
            tbl = vault_datatable.read_table(path)
        except Exception:
            continue
        folder = posixpath.dirname(path)
        out.append({
            "path": path,
            "folder": folder,
            "title": _table_title(tbl["schema"], path),
            "row_count": len(tbl["rows"]),
            "field_count": len(tbl["schema"].get("fields", [])) if isinstance(tbl["schema"], dict) else 0,
        })
    return out


def list_databases() -> list[dict[str, Any]]:
    """Return ``[{folder, title, table_count}]`` for every database in the vault.

    A database is a folder (including the root, ``""``) that contains ≥1
    data-table file. Sorted by lower-cased title for stable rendering.
    """
    by_folder: dict[str, int] = {}
    for tbl in _walk_tables():
        by_folder[tbl["folder"]] = by_folder.get(tbl["folder"], 0) + 1
    out = [
        {"folder": folder, "title": _database_title(folder), "table_count": count}
        for folder, count in by_folder.items()
    ]
    out.sort(key=lambda d: d["title"].lower())
    return out


def list_tables_in_folder(folder: str) -> list[dict[str, Any]]:
    """Return ``[{path, title, row_count, field_count}]`` for tables in ``folder``.

    ``folder`` is matched exactly (no recursion into subfolders). Use ``""`` for
    the vault root. Sorted by lower-cased title.
    """
    folder = folder.strip("/")
    out = [
        {
            "path": tbl["path"],
            "title": tbl["title"],
            "row_count": tbl["row_count"],
            "field_count": tbl["field_count"],
        }
        for tbl in _walk_tables()
        if tbl["folder"] == folder
    ]
    out.sort(key=lambda t: t["title"].lower())
    return out


# ── ER diagram ───────────────────────────────────────────────────────────────


_ER_NAME_SAFE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"


def _erd_node_name(path: str) -> str:
    """Mermaid erDiagram entity names must be alphanumeric/underscore."""
    stem = path.rsplit("/", 1)[-1].removesuffix(".md")
    cleaned = "".join(c if c in _ER_NAME_SAFE else "_" for c in stem)
    return cleaned or "table"


def _ref_field_target(host_path: str, field: dict[str, Any]) -> str:
    return vault_datatable.resolve_ref(host_path, field.get("target_table") or "")


def er_diagram(folder: str) -> str:
    """Generate mermaid ``erDiagram`` source for every table in ``folder``.

    Cardinality mapping:
      * ``cardinality: one``  → ``}o--||`` (zero-or-many to exactly-one).
      * Junction with two refs and no payload → collapse to a single
        ``}o--o{`` line between the two referenced tables.
      * Junction with extra payload columns → keep the junction as a node
        with two ``}o--||`` lines into the referenced tables.

    Tables outside ``folder`` referenced by a ref still appear as nodes so
    the cross-database relationship is visible.
    """
    folder = folder.strip("/")
    in_folder = [tbl for tbl in _walk_tables() if tbl["folder"] == folder]
    if not in_folder:
        return "erDiagram"

    # Collect schemas keyed by path.
    schemas: dict[str, dict[str, Any]] = {}
    for tbl in in_folder:
        try:
            full = vault_datatable.read_table(tbl["path"])
        except Exception:
            continue
        schemas[tbl["path"]] = full["schema"]

    nodes: dict[str, str] = {}  # path → ER node name
    edges: list[str] = []

    def node(path: str) -> str:
        if path not in nodes:
            base = _erd_node_name(path)
            # Disambiguate collisions across folders.
            existing = set(nodes.values())
            name = base
            i = 2
            while name in existing:
                name = f"{base}_{i}"
                i += 1
            nodes[path] = name
        return nodes[path]

    for path, schema in schemas.items():
        if vault_datatable.is_junction(schema):
            refs = [f for f in schema.get("fields", []) if isinstance(f, dict) and f.get("kind") == "ref"]
            if len(refs) == 2:
                a_target = _ref_field_target(path, refs[0])
                b_target = _ref_field_target(path, refs[1])
                if a_target and b_target:
                    # Pure 2-ref junction → collapse to a single edge between
                    # the referenced tables. Don't register the junction as a
                    # node; otherwise we'd render a redundant tile on top of
                    # the collapsed edge.
                    a_name = node(a_target)
                    b_name = node(b_target)
                    label = path.rsplit("/", 1)[-1].removesuffix(".md")
                    edges.append(f"    {a_name} }}o--o{{ {b_name} : \"{label}\"")
                    continue
            # Fall through: junction with non-standard shape → render as node.
        host_node = node(path)
        for f in schema.get("fields", []):
            if not isinstance(f, dict) or f.get("kind") != "ref":
                continue
            target = _ref_field_target(path, f)
            if not target:
                continue
            target_name = node(target)
            field_label = f.get("name", "ref")
            cardinality = f.get("cardinality", "one")
            if cardinality == "many":
                edges.append(f"    {host_node} }}o--o{{ {target_name} : \"{field_label}\"")
            else:
                edges.append(f"    {host_node} }}o--|| {target_name} : \"{field_label}\"")

    # Emit entities with their fields.
    body: list[str] = ["erDiagram"]
    seen_entities: set[str] = set()
    for path, name in nodes.items():
        if name in seen_entities:
            continue
        seen_entities.add(name)
        schema = schemas.get(path)
        if not schema:
            body.append(f"    {name} {{")
            body.append("        string _id PK")
            body.append("    }")
            continue
        body.append(f"    {name} {{")
        for f in schema.get("fields", []):
            if not isinstance(f, dict):
                continue
            fname = f.get("name") or ""
            if not fname:
                continue
            kind = f.get("kind", "text")
            type_str = "string"
            if kind == "number":
                type_str = "float"
            elif kind == "boolean":
                type_str = "bool"
            elif kind == "date":
                type_str = "date"
            elif kind == "ref":
                type_str = "ref"
            safe = "".join(c if c in _ER_NAME_SAFE else "_" for c in fname) or "field"
            tbl_meta = schema.get("table") if isinstance(schema, dict) else None
            pk = (
                tbl_meta.get("primary_key")
                if isinstance(tbl_meta, dict) and tbl_meta.get("primary_key")
                else None
            )
            suffix = " PK" if fname == pk else (" FK" if kind == "ref" else "")
            body.append(f'        {type_str} "{safe}"{suffix}')
        body.append("    }")

    body.extend(edges)
    return "\n".join(body)
