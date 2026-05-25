from __future__ import annotations

import posixpath
from typing import Any

from . import vault
from ._vault_datatable_core import (
    _load_state,
    _ref_fields,
    _rollup_fields,
    is_datatable_file,
    read_table,
)
from .vault_datatable_schema import add_field, create_table


def resolve_ref(host_path: str, target_table: str) -> str:
    if not target_table:
        return ""
    t = target_table.strip()
    if not t.startswith(".") and not t.startswith("/"):
        return posixpath.normpath(t)
    host_dir = posixpath.dirname(host_path or "")
    joined = posixpath.normpath(posixpath.join(host_dir, t.lstrip("/")))
    return joined


def is_junction(schema: dict[str, Any]) -> bool:
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


def _pk_name(schema: dict[str, Any]) -> str:
    table_meta = schema.get("table") if isinstance(schema, dict) else None
    if isinstance(table_meta, dict) and table_meta.get("primary_key"):
        return table_meta["primary_key"]
    return "_id"


def _formula_fields(schema: dict[str, Any]) -> list[dict[str, Any]]:
    fields = schema.get("fields") if isinstance(schema, dict) else None
    if not isinstance(fields, list):
        return []
    return [
        f for f in fields
        if isinstance(f, dict) and f.get("kind") == "formula" and f.get("formula")
    ]


def materialize(
    path: str,
    rows: list[dict[str, Any]] | None = None,
    schema: dict[str, Any] | None = None,
    *,
    _cache: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    from . import vault_formula

    if rows is None or schema is None:
        tbl = read_table(path)
        if rows is None:
            rows = tbl["rows"]
        if schema is None:
            schema = tbl["schema"]

    enriched = [dict(r) for r in rows]

    formula_flds = _formula_fields(schema)
    rollup_flds = _rollup_fields(schema)

    if not formula_flds and not rollup_flds:
        return enriched

    for f in formula_flds:
        expr = f["formula"]
        for row in enriched:
            row[f["name"]] = vault_formula.eval_formula(expr, row)

    if rollup_flds:
        if _cache is None:
            _cache = {}
        pk = _pk_name(schema)
        for rf in rollup_flds:
            target_path = resolve_ref(path, rf.get("rollup_target_table", ""))
            if not target_path:
                continue
            if target_path in _cache:
                target_rows = _cache[target_path]
            else:
                try:
                    target_tbl = read_table(target_path)
                except (FileNotFoundError, OSError):
                    continue
                target_rows = materialize(
                    target_path,
                    target_tbl["rows"],
                    target_tbl["schema"],
                    _cache=_cache,
                )
                _cache[target_path] = target_rows

            filter_expr = rf.get("rollup_filter")
            if filter_expr:
                filtered = [
                    r for r in target_rows
                    if _truthy_materialize(vault_formula.eval_formula(filter_expr, r))
                ]
            else:
                filtered = target_rows

            rel_field = rf.get("rollup_relation_field", "")
            agg = rf.get("rollup_aggregate", "sum")
            src_field = rf.get("rollup_source_field")

            grouped: dict[str, list[Any]] = {}
            for detail in filtered:
                fk_val = str(detail.get(rel_field, "")).strip()
                if not fk_val:
                    continue
                grouped.setdefault(fk_val, []).append(
                    detail.get(src_field, 1) if src_field else 1
                )

            for row in enriched:
                pk_val = str(row.get(pk, row.get("_id", ""))).strip()
                group = grouped.get(pk_val)
                if not group:
                    row[rf["name"]] = 0 if agg == "count" else ""
                    continue
                nums = [_to_num_materialize(v) for v in group]
                row[rf["name"]] = _aggregate(nums, agg)

    for f in formula_flds:
        expr = f["formula"]
        for row in enriched:
            row[f["name"]] = vault_formula.eval_formula(expr, row)

    return enriched


def _to_num_materialize(v: Any) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def _truthy_materialize(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v != ""
    return bool(v)


def _aggregate(values: list[float], fn: str) -> Any:
    if fn == "count":
        return len(values)
    if not values:
        return ""
    s = sum(values)
    if fn == "sum":
        return round(s * 1_000_000) / 1_000_000
    if fn == "avg":
        return round((s / len(values)) * 1_000_000) / 1_000_000
    if fn == "min":
        return min(values)
    if fn == "max":
        return max(values)
    return ""


def validate_refs(
    path: str, row: dict[str, Any],
) -> list[str]:
    warnings: list[str] = []
    fm, tbl = _load_state(path)
    schema = tbl.get("schema", {})
    for f in _ref_fields(schema):
        fname = f.get("name", "")
        raw_val = row.get(fname)
        if raw_val is None or raw_val == "":
            continue
        target = f.get("target_table", "")
        resolved = resolve_ref(path, target)
        if not resolved:
            continue
        try:
            tgt = read_table(resolved)
        except (FileNotFoundError, OSError):
            continue
        tgt_schema = tgt.get("schema", {})
        tgt_meta = tgt_schema.get("table") if isinstance(tgt_schema, dict) else None
        pk_name = ""
        if isinstance(tgt_meta, dict):
            pk_name = tgt_meta.get("primary_key", "")
        ids = raw_val if isinstance(raw_val, list) else [raw_val]
        for ref_id in ids:
            ref_str = str(ref_id).strip()
            found = any(
                str(r.get(pk_name, "")).strip() == ref_str
                or str(r.get("_id", "")).strip() == ref_str
                or any(str(v).strip() == ref_str for v in r.values() if isinstance(v, (str, int, float)))
                for r in tgt.get("rows", [])
                if isinstance(r, dict)
            )
            if not found:
                warnings.append(
                    f"ref '{fname}' value '{ref_str}' not found in {resolved} "
                    f"(no row with pk/{pk_name or '_id'} matching '{ref_str}')"
                )
    return warnings


def _validate_refs(
    path: str, schema: dict[str, Any], row: dict[str, Any],
) -> list[str]:
    warnings: list[str] = []
    for f in _ref_fields(schema):
        fname = f.get("name", "")
        raw_val = row.get(fname)
        if raw_val is None or raw_val == "":
            continue
        target = f.get("target_table", "")
        resolved = resolve_ref(path, target)
        if not resolved:
            continue
        try:
            tbl = read_table(resolved)
        except (FileNotFoundError, OSError):
            continue
        tbl_schema = tbl.get("schema", {})
        tbl_meta = tbl_schema.get("table") if isinstance(tbl_schema, dict) else None
        pk_name = ""
        if isinstance(tbl_meta, dict):
            pk_name = tbl_meta.get("primary_key", "")
        ids = raw_val if isinstance(raw_val, list) else [raw_val]
        for ref_id in ids:
            ref_str = str(ref_id).strip()
            found = any(
                str(r.get(pk_name, "")).strip() == ref_str
                or str(r.get("_id", "")).strip() == ref_str
                or any(str(v).strip() == ref_str for v in r.values() if isinstance(v, (str, int, float)))
                for r in tbl.get("rows", [])
                if isinstance(r, dict)
            )
            if not found:
                warnings.append(
                    f"ref '{fname}' value '{ref_str}' not found in {resolved} "
                    f"(no row with pk/{pk_name or '_id'} matching '{ref_str}')"
                )
    return warnings


def create_relation(
    from_table: str,
    field_name: str,
    target_table: str,
    cardinality: str = "one",
    *,
    label: str | None = None,
) -> dict[str, Any]:
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


def _norm_ref_value(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _row_matches_ref(
    row: dict[str, Any], field_name: str, expected_ids: set[str],
) -> bool:
    val = row.get(field_name)
    if val is None:
        return False
    if isinstance(val, list):
        return any(_norm_ref_value(v) in expected_ids for v in val)
    return _norm_ref_value(val) in expected_ids


def _expected_ref_ids(table_path: str, row_id: str) -> set[str]:
    expected: set[str] = {row_id.strip()}
    try:
        host = read_table(table_path)
    except Exception:
        return expected
    schema = host.get("schema") or {}
    table_meta = schema.get("table") if isinstance(schema, dict) else None
    pk_name = (
        table_meta.get("primary_key")
        if isinstance(table_meta, dict) and table_meta.get("primary_key")
        else None
    )
    if not pk_name:
        return expected
    target = row_id.strip()
    for r in host.get("rows", []):
        if _norm_ref_value(r.get(pk_name)) == target:
            _id = r.get("_id")
            if _id is not None:
                expected.add(_norm_ref_value(_id))
            break
    return expected


def _collect_unmatched_sample(
    rows: list[dict[str, Any]], field_name: str, limit: int = 3,
) -> list[str]:
    seen: list[str] = []
    seen_set: set[str] = set()
    for r in rows:
        v = r.get(field_name)
        if v is None:
            continue
        candidates = v if isinstance(v, list) else [v]
        for c in candidates:
            s = _norm_ref_value(c)
            if not s or s in seen_set:
                continue
            seen.append(s)
            seen_set.add(s)
            if len(seen) >= limit:
                return seen
    return seen


def related_rows(table_path: str, row_id: str) -> dict[str, Any]:
    expected_ids = _expected_ref_ids(table_path, row_id)
    one_to_many: list[dict[str, Any]] = []
    many_to_many: list[dict[str, Any]] = []
    for ref in find_inbound_refs(table_path):
        try:
            from_tbl = read_table(ref["from_table"])
        except Exception:
            continue
        matches = [
            r for r in from_tbl["rows"]
            if _row_matches_ref(r, ref["field_name"], expected_ids)
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
                        s = _norm_ref_value(x)
                        if s:
                            wanted_ids.add(s)
                else:
                    s = _norm_ref_value(v)
                    if s:
                        wanted_ids.add(s)
            target_rows = [
                r for r in target_tbl["rows"]
                if _norm_ref_value(r.get(target_pk, r.get("_id"))) in wanted_ids
            ]
            target_title = (
                target_tbl["schema"].get("title")
                if isinstance(target_tbl["schema"], dict)
                and isinstance(target_tbl["schema"].get("title"), str)
                else target.rsplit("/", 1)[-1].removesuffix(".md")
            )
            if target_rows:
                unmatched_sample: list[str] = []
            elif not matches:
                unmatched_sample = _collect_unmatched_sample(
                    from_tbl["rows"], ref["field_name"],
                )
            else:
                unmatched_sample = sorted(wanted_ids)[:3]
            many_to_many.append({
                "junction_table": ref["from_table"],
                "junction_title": ref["from_title"],
                "target_table": target,
                "target_title": target_title,
                "rows": target_rows,
                "count": len(target_rows),
                "unmatched_sample": unmatched_sample,
            })
        else:
            one_to_many.append({
                "from_table": ref["from_table"],
                "from_title": ref["from_title"],
                "field_name": ref["field_name"],
                "cardinality": ref["cardinality"],
                "rows": matches,
                "count": len(matches),
                "unmatched_sample": (
                    [] if matches else _collect_unmatched_sample(
                        from_tbl["rows"], ref["field_name"],
                    )
                ),
            })
    return {"one_to_many": one_to_many, "many_to_many": many_to_many}
