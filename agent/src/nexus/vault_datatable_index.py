"""Discovery layer for vault data-tables grouped into "databases."

A folder containing ≥1 file with ``data-table-plugin: basic`` frontmatter is
treated as a database. Results are cached in-process and persisted to disk,
invalidated on vault file changes.
"""

from __future__ import annotations

import json
import logging
import posixpath
import threading
from pathlib import Path
from typing import Any

from . import vault, vault_datatable

log = logging.getLogger(__name__)

_CACHE_PATH = Path.home() / ".nexus" / ".datatable_cache.json"


def _table_title(schema: dict[str, Any], path: str) -> str:
    title = schema.get("title") if isinstance(schema, dict) else None
    if isinstance(title, str) and title.strip():
        return title.strip()
    stem = path.rsplit("/", 1)[-1].removesuffix(".md")
    return stem or path


def _database_title(folder: str) -> str:
    if not folder:
        return "(root)"
    raw = folder.rsplit("/", 1)[-1] or folder
    return raw[0].upper() + raw[1:] if raw else raw


def _walk_tables() -> list[dict[str, Any]]:
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


def _scan_databases(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from . import vault_dashboard
    by_folder: dict[str, int] = {}
    for tbl in tables:
        by_folder[tbl["folder"]] = by_folder.get(tbl["folder"], 0) + 1
    out: list[dict[str, Any]] = []
    for folder, count in by_folder.items():
        dash = vault_dashboard.read_dashboard(folder)
        out.append({
            "folder": folder,
            "title": _database_title(folder),
            "icon": dash.get("icon"),
            "table_count": count,
        })
    out.sort(key=lambda d: d["title"].lower())
    return out


class _DatabaseCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._databases: list[dict[str, Any]] | None = None
        self._tables: list[dict[str, Any]] | None = None

    def invalidate(self) -> None:
        with self._lock:
            self._databases = None
            self._tables = None
        try:
            if _CACHE_PATH.exists():
                _CACHE_PATH.unlink()
        except Exception:
            pass

    def get_databases(self) -> list[dict[str, Any]]:
        with self._lock:
            if self._databases is not None:
                return list(self._databases)
            self._load_disk_locked()
            if self._databases is not None:
                return list(self._databases)
            self._scan_locked()
            return list(self._databases) if self._databases else []

    def get_tables(self, folder: str) -> list[dict[str, Any]]:
        folder = folder.strip("/")
        with self._lock:
            if self._tables is not None:
                return [t for t in self._tables if t["folder"] == folder]
            self._load_disk_locked()
            if self._tables is not None:
                return [t for t in self._tables if t["folder"] == folder]
            self._scan_locked()
            return [t for t in (self._tables or []) if t["folder"] == folder]

    def _scan_locked(self) -> None:
        tables = _walk_tables()
        databases = _scan_databases(tables)
        self._tables = tables
        self._databases = databases
        self._persist_locked()

    def _load_disk_locked(self) -> None:
        try:
            if not _CACHE_PATH.exists():
                return
            raw = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            tables = raw.get("tables")
            databases = raw.get("databases")
            if isinstance(tables, list) and isinstance(databases, list):
                self._tables = tables
                self._databases = databases
        except Exception:
            log.debug("datatable cache: disk load failed", exc_info=True)

    def _persist_locked(self) -> None:
        try:
            _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = _CACHE_PATH.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({"tables": self._tables, "databases": self._databases}),
                encoding="utf-8",
            )
            tmp.replace(_CACHE_PATH)
        except Exception:
            log.debug("datatable cache: disk persist failed", exc_info=True)

    def warm(self) -> None:
        with self._lock:
            if self._databases is not None:
                return
            self._load_disk_locked()
            if self._databases is None:
                self._scan_locked()


_cache = _DatabaseCache()


def invalidate_cache() -> None:
    _cache.invalidate()


def warm_cache() -> None:
    _cache.warm()


def list_databases() -> list[dict[str, Any]]:
    return _cache.get_databases()


def list_tables_in_folder(folder: str) -> list[dict[str, Any]]:
    folder = folder.strip("/")
    out = [
        {
            "path": tbl["path"],
            "title": tbl["title"],
            "row_count": tbl["row_count"],
            "field_count": tbl["field_count"],
        }
        for tbl in _cache.get_tables(folder)
    ]
    out.sort(key=lambda t: t["title"].lower())
    return out


# ── ER diagram ───────────────────────────────────────────────────────────────

_ER_NAME_SAFE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
_MERMAID_BLOCK_KEYWORDS = frozenset({"pk", "fk", "uk"})


def _erd_node_name(path: str) -> str:
    stem = path.rsplit("/", 1)[-1].removesuffix(".md")
    cleaned = "".join(c if c in _ER_NAME_SAFE else "_" for c in stem)
    return cleaned or "table"


def _ref_field_target(host_path: str, field: dict[str, Any]) -> str:
    return vault_datatable.resolve_ref(host_path, field.get("target_table") or "")


def er_diagram(folder: str) -> str:
    folder = folder.strip("/")
    in_folder = [tbl for tbl in _walk_tables() if tbl["folder"] == folder]
    if not in_folder:
        return "erDiagram"

    schemas: dict[str, dict[str, Any]] = {}
    for tbl in in_folder:
        try:
            full = vault_datatable.read_table(tbl["path"])
        except Exception:
            continue
        schemas[tbl["path"]] = full["schema"]

    nodes: dict[str, str] = {}
    edges: list[str] = []

    def node(path: str) -> str:
        if path not in nodes:
            base = _erd_node_name(path)
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
                    a_name = node(a_target)
                    b_name = node(b_target)
                    label = path.rsplit("/", 1)[-1].removesuffix(".md")
                    edges.append(f"    {a_name} }}o--o{{ {b_name} : \"{label}\"")
                    continue
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
            if safe.lower() in _MERMAID_BLOCK_KEYWORDS:
                safe += "_"
            tbl_meta = schema.get("table") if isinstance(schema, dict) else None
            pk = (
                tbl_meta.get("primary_key")
                if isinstance(tbl_meta, dict) and tbl_meta.get("primary_key")
                else None
            )
            suffix = " PK" if fname == pk else (" FK" if kind == "ref" else "")
            body.append(f"        {type_str} {safe}{suffix}")
        body.append("    }")

    body.extend(edges)
    return "\n".join(body)
