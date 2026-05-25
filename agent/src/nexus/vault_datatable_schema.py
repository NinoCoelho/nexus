from __future__ import annotations

from typing import Any

from . import vault
from ._vault_datatable_core import (
    DATATABLE_PLUGIN_KEY,
    _load_state,
    _ref_fields,
    _rollup_fields,
    _serialize,
)


def validate_schema(schema: dict[str, Any], host_path: str = "") -> list[str]:
    warnings: list[str] = []
    from . import vault_formula

    all_fields = schema.get("fields") if isinstance(schema, dict) else None
    if isinstance(all_fields, list):
        for f in all_fields:
            if not isinstance(f, dict):
                continue
            name = f.get("name") or "(unnamed)"
            kind = f.get("kind")
            if kind == "formula":
                expr = f.get("formula")
                if expr and isinstance(expr, str):
                    errs = vault_formula.validate_formula(expr)
                    for e in errs:
                        warnings.append(f"field {name!r}: invalid formula: {e}")
            elif kind == "rollup":
                if f.get("rollup_filter"):
                    errs = vault_formula.validate_formula(f["rollup_filter"])
                    for e in errs:
                        warnings.append(f"field {name!r}: invalid rollup_filter: {e}")

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
    for f in _rollup_fields(schema):
        name = f.get("name") or "(unnamed)"
        target = f.get("rollup_target_table")
        if not target or not isinstance(target, str) or not target.strip():
            warnings.append(f"field {name!r}: kind=rollup needs a rollup_target_table")
        rel_field = f.get("rollup_relation_field")
        if not rel_field or not isinstance(rel_field, str) or not rel_field.strip():
            warnings.append(f"field {name!r}: kind=rollup needs a rollup_relation_field")
        agg = f.get("rollup_aggregate")
        if agg not in ("sum", "count", "avg", "min", "max"):
            warnings.append(
                f"field {name!r}: rollup_aggregate={agg!r} must be one of sum/count/avg/min/max"
            )
        if agg != "count":
            src = f.get("rollup_source_field")
            if not src or not isinstance(src, str) or not src.strip():
                warnings.append(f"field {name!r}: kind=rollup needs a rollup_source_field for aggregate={agg!r}")
    return warnings


def create_table(path: str, schema: dict[str, Any]) -> dict[str, Any]:
    fm = {DATATABLE_PLUGIN_KEY: "basic"}
    vault.write_file(path, _serialize(fm, schema, [], []))
    return {"schema": schema, "rows": [], "views": []}


def set_schema(path: str, schema: dict[str, Any]) -> dict[str, Any]:
    fm, tbl = _load_state(path)
    vault.write_file(path, _serialize(fm, schema, tbl["rows"], tbl.get("views", [])))
    return {"schema": schema, "rows": tbl["rows"], "views": tbl.get("views", [])}


def set_views(path: str, views: list[dict[str, Any]]) -> dict[str, Any]:
    fm, tbl = _load_state(path)
    vault.write_file(path, _serialize(fm, tbl["schema"], tbl["rows"], views))
    return {"schema": tbl["schema"], "rows": tbl["rows"], "views": views}


def add_field(path: str, field: dict[str, Any]) -> dict[str, Any]:
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
    fm, tbl = _load_state(path)
    schema = tbl["schema"] or {}
    fields = [
        f for f in schema.get("fields", [])
        if not (isinstance(f, dict) and f.get("name") == field_name)
    ]
    schema["fields"] = fields
    vault.write_file(path, _serialize(fm, schema, tbl["rows"], tbl.get("views", [])))
    return {"schema": schema, "rows": tbl["rows"], "views": tbl.get("views", [])}


def update_field(path: str, field_name: str, updates: dict[str, Any]) -> dict[str, Any]:
    fm, tbl = _load_state(path)
    schema = tbl["schema"] or {}
    fields = schema.get("fields", [])
    found = False
    new_name = updates.get("name")
    for f in fields:
        if isinstance(f, dict) and f.get("name") == field_name:
            f.update(updates)
            if new_name and new_name != field_name:
                for r in tbl["rows"]:
                    if field_name in r:
                        r[new_name] = r.pop(field_name)
            found = True
            break
    if not found:
        raise KeyError(f"field {field_name!r} not found")
    warnings = validate_schema(schema, path)
    vault.write_file(path, _serialize(fm, schema, tbl["rows"], tbl.get("views", [])))
    return {
        "schema": schema,
        "rows": tbl["rows"],
        "views": tbl.get("views", []),
        "warnings": warnings,
    }


def rename_field(path: str, old_name: str, new_name: str) -> dict[str, Any]:
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
