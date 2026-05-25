from __future__ import annotations

import uuid
from typing import Any

from . import vault
from ._vault_datatable_core import _load_state, _serialize, read_table


def add_row(path: str, row: dict[str, Any]) -> dict[str, Any]:
    fm, tbl = _load_state(path)
    if "_id" not in row:
        row = {"_id": uuid.uuid4().hex[:8], **row}
    tbl["rows"].append(row)
    vault.write_file(path, _serialize(fm, tbl["schema"], tbl["rows"], tbl.get("views", [])))
    return row


def add_rows_with_report(
    path: str,
    rows: list[Any],
    *,
    required_fields: list[str] | None = None,
) -> dict[str, Any]:
    fm, tbl = _load_state(path)
    added: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    required = list(required_fields or [])
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            skipped.append({
                "index": i,
                "reason": f"not an object: {type(r).__name__}",
            })
            continue
        missing = [f for f in required if r.get(f) in (None, "")]
        if missing:
            skipped.append({
                "index": i,
                "reason": f"missing required field(s): {', '.join(missing)}",
            })
            continue
        if "_id" not in r:
            r = {"_id": uuid.uuid4().hex[:8], **r}
        tbl["rows"].append(r)
        added.append(r)
    vault.write_file(path, _serialize(fm, tbl["schema"], tbl["rows"], tbl.get("views", [])))
    return {"added": added, "skipped": skipped}


def add_rows(path: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return add_rows_with_report(path, rows)["added"]


def find_rows(
    path: str,
    *,
    where: dict[str, Any] | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    if not where and not (q and q.strip()):
        raise ValueError("find_rows requires `where` and/or `q`")
    tbl = read_table(path)
    rows = tbl["rows"]
    schema = tbl["schema"] or {}
    fields = schema.get("fields") or []
    text_fields = [
        f.get("name") for f in fields
        if isinstance(f, dict)
        and f.get("name")
        and f.get("kind", "text") in ("text", "textarea")
    ]
    needle = q.strip().lower() if isinstance(q, str) and q.strip() else None
    where = where or {}

    def _matches(row: dict[str, Any]) -> bool:
        for k, v in where.items():
            cell = row.get(k)
            if isinstance(cell, list):
                if v not in cell:
                    return False
            elif cell != v:
                return False
        if needle is not None:
            scan = list(text_fields)
            if "_id" not in scan:
                scan.append("_id")
            for fname in scan:
                cell = row.get(fname)
                if cell is None:
                    continue
                if needle in str(cell).lower():
                    return True
            return False
        return True

    matched = [r for r in rows if _matches(r)]
    total = len(matched)
    page = matched[offset : offset + limit]
    return {
        "rows": page,
        "count": len(page),
        "total": total,
        "offset": offset,
        "limit": limit,
        "truncated": (offset + len(page)) < total,
    }


def update_row(path: str, row_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    fm, tbl = _load_state(path)
    for row in tbl["rows"]:
        if str(row.get("_id")) == str(row_id):
            row.update({k: v for k, v in updates.items() if k != "_id"})
            vault.write_file(path, _serialize(fm, tbl["schema"], tbl["rows"], tbl.get("views", [])))
            return row
    raise KeyError(f"row {row_id!r} not found")


def delete_row(path: str, row_id: str) -> None:
    fm, tbl = _load_state(path)
    before = len(tbl["rows"])
    tbl["rows"] = [r for r in tbl["rows"] if str(r.get("_id")) != str(row_id)]
    if len(tbl["rows"]) == before:
        raise KeyError(f"row {row_id!r} not found")
    vault.write_file(path, _serialize(fm, tbl["schema"], tbl["rows"], tbl.get("views", [])))
