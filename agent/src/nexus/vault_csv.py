"""DuckDB-backed analytics over CSV files in the vault.

All public functions take a vault-relative path and return JSON-serializable
dicts. The engine never loads the full CSV into Python memory — DuckDB streams
from disk via ``read_csv_auto``.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any

import duckdb

from . import vault

_MAX_EDITABLE_BYTES = 50 * 1024 * 1024  # 50MB hard cap on UI edits

_DEFAULT_QUERY_LIMIT = 200
# Cap chosen so that worst-case payload (1000 rows × ~10 wide cols ≈ ~96 tok/row)
# stays under ~100k tokens — fits in a 200k context with room to spare.
_MAX_QUERY_LIMIT = 1000
_RELATIONSHIP_OVERLAP_THRESHOLD = 0.5
_RELATIONSHIP_MAX_CANDIDATES = 20


def _resolve(rel_path: str) -> Path:
    full = vault.resolve_path(rel_path)
    if not full.exists():
        raise FileNotFoundError(f"vault file not found: {rel_path}")
    if not full.is_file():
        raise ValueError(f"not a file: {rel_path}")
    if full.suffix.lower() not in (".csv", ".tsv"):
        raise ValueError(f"not a CSV/TSV file: {rel_path}")
    return full


def _connect() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(database=":memory:")


def _sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _register(con: duckdb.DuckDBPyConnection, full: Path, view: str = "t") -> None:
    # read_csv_auto sniffs delimiter, header, and types. DuckDB does not
    # accept prepared parameters in DDL, so we inline the path as an escaped
    # string literal — the path was already validated by _resolve.
    path_lit = _sql_str(str(full))
    con.execute(
        f"CREATE OR REPLACE VIEW {view} AS "
        f"SELECT * FROM read_csv_auto({path_lit}, sample_size=4096)"
    )


def csv_schema(rel_path: str) -> dict[str, Any]:
    full = _resolve(rel_path)
    con = _connect()
    try:
        _register(con, full)
        cols = con.execute("DESCRIBE t").fetchall()
        # DESCRIBE returns (column_name, column_type, null, key, default, extra)
        columns = [{"name": r[0], "type": r[1]} for r in cols]
        row_count = con.execute("SELECT count(*) FROM t").fetchone()[0]
    finally:
        con.close()
    return {
        "path": rel_path,
        "columns": columns,
        "row_count": int(row_count),
        "file_size": full.stat().st_size,
    }


def csv_sample(rel_path: str, mode: str = "head", n: int = 20) -> dict[str, Any]:
    if n <= 0 or n > 1000:
        raise ValueError("`n` must be between 1 and 1000")
    if mode not in ("head", "tail", "random"):
        raise ValueError("`mode` must be one of head/tail/random")
    full = _resolve(rel_path)
    con = _connect()
    try:
        _register(con, full)
        if mode == "head":
            sql = f"SELECT * FROM t LIMIT {n}"
        elif mode == "tail":
            sql = (
                f"SELECT * FROM (SELECT *, row_number() OVER () AS __rn FROM t) "
                f"ORDER BY __rn DESC LIMIT {n}"
            )
        else:  # random
            sql = f"SELECT * FROM t USING SAMPLE {n} ROWS"
        rel = con.execute(sql)
        columns = [d[0] for d in rel.description if d[0] != "__rn"]
        rows = rel.fetchall()
        if mode == "tail":
            # Drop the synthetic __rn column we added for ordering.
            rn_idx = [d[0] for d in rel.description].index("__rn")
            rows = [tuple(v for i, v in enumerate(r) if i != rn_idx) for r in rows]
    finally:
        con.close()
    return {
        "path": rel_path,
        "mode": mode,
        "columns": columns,
        "rows": [_row_to_dict(columns, r) for r in rows],
    }


def csv_describe(rel_path: str, columns: list[str] | None = None) -> dict[str, Any]:
    full = _resolve(rel_path)
    con = _connect()
    try:
        _register(con, full)
        schema = con.execute("DESCRIBE t").fetchall()
        col_types = {r[0]: r[1] for r in schema}
        target = columns or list(col_types.keys())
        out: list[dict[str, Any]] = []
        for col in target:
            if col not in col_types:
                out.append({"column": col, "error": "unknown column"})
                continue
            t = col_types[col].upper()
            is_numeric = any(k in t for k in ("INT", "DECIMAL", "DOUBLE", "FLOAT", "REAL", "NUMERIC", "HUGEINT"))
            quoted = _quote_ident(col)
            stats: dict[str, Any] = {"column": col, "type": col_types[col]}
            row = con.execute(
                f"SELECT count({quoted}), count(*) - count({quoted}), "
                f"count(DISTINCT {quoted}) FROM t"
            ).fetchone()
            stats["count"] = int(row[0])
            stats["null_count"] = int(row[1])
            stats["distinct_count"] = int(row[2])
            if is_numeric:
                row = con.execute(
                    f"SELECT min({quoted}), max({quoted}), avg({quoted}), stddev({quoted}) FROM t"
                ).fetchone()
                stats["min"] = row[0]
                stats["max"] = row[1]
                stats["mean"] = row[2]
                stats["stddev"] = row[3]
            else:
                top = con.execute(
                    f"SELECT {quoted} AS v, count(*) AS c FROM t "
                    f"WHERE {quoted} IS NOT NULL GROUP BY v ORDER BY c DESC LIMIT 5"
                ).fetchall()
                stats["top_values"] = [{"value": r[0], "count": int(r[1])} for r in top]
            out.append(stats)
    finally:
        con.close()
    return {"path": rel_path, "stats": out}


_SQL_LEAD_RE = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)


def csv_query(
    rel_path: str,
    sql: str,
    limit: int = _DEFAULT_QUERY_LIMIT,
    fmt: str = "columns",
) -> dict[str, Any]:
    if not _SQL_LEAD_RE.match(sql or ""):
        raise ValueError("only SELECT / WITH queries are allowed")
    if fmt not in ("columns", "rows"):
        raise ValueError("`fmt` must be 'columns' or 'rows'")
    if limit <= 0:
        limit = _DEFAULT_QUERY_LIMIT
    limit = min(int(limit), _MAX_QUERY_LIMIT)
    full = _resolve(rel_path)
    con = _connect()
    try:
        _register(con, full)
        # Execute as subquery so we can tack on LIMIT and a total count without
        # parsing the user's SQL.
        rel = con.execute(f"SELECT * FROM ({sql}) AS __q LIMIT {limit + 1}")
        columns = [d[0] for d in rel.description]
        rows = rel.fetchall()
        truncated = len(rows) > limit
        if truncated:
            rows = rows[:limit]
        # Compute total only when truncation hit; otherwise we already know.
        if truncated:
            total = con.execute(f"SELECT count(*) FROM ({sql}) AS __q").fetchone()[0]
        else:
            total = len(rows)
    finally:
        con.close()
    payload: dict[str, Any] = {
        "path": rel_path,
        "sql": sql,
        "columns": columns,
        "row_count": int(total),
        "truncated": truncated,
        "limit": limit,
        "format": fmt,
    }
    if fmt == "columns":
        payload["data"] = [list(r) for r in rows]
    else:
        payload["rows"] = [_row_to_dict(columns, r) for r in rows]
    return payload


def csv_relationships(rel_path: str, candidates: list[str] | None = None) -> dict[str, Any]:
    full = _resolve(rel_path)
    if candidates is None:
        candidates = _list_other_csvs(rel_path)
    candidates = candidates[:_RELATIONSHIP_MAX_CANDIDATES]

    con = _connect()
    matches: list[dict[str, Any]] = []
    try:
        _register(con, full, "t1")
        cols1 = [r[0] for r in con.execute("DESCRIBE t1").fetchall()]
        for other in candidates:
            try:
                other_full = _resolve(other)
            except (FileNotFoundError, ValueError):
                continue
            try:
                con.execute(
                    f"CREATE OR REPLACE VIEW t2 AS "
                    f"SELECT * FROM read_csv_auto({_sql_str(str(other_full))}, sample_size=4096)"
                )
                cols2 = [r[0] for r in con.execute("DESCRIBE t2").fetchall()]
            except duckdb.Error:
                continue
            for a in cols1:
                for b in cols2:
                    name_score = _name_similarity(a, b)
                    if name_score < 0.4:
                        continue
                    try:
                        overlap, sample = _value_overlap(con, a, b)
                    except duckdb.Error:
                        continue
                    if overlap < _RELATIONSHIP_OVERLAP_THRESHOLD:
                        continue
                    matches.append({
                        "left": {"path": rel_path, "column": a},
                        "right": {"path": other, "column": b},
                        "name_score": round(name_score, 3),
                        "value_overlap": round(overlap, 3),
                        "score": round(name_score * 0.4 + overlap * 0.6, 3),
                        "sample_matches": sample,
                    })
    finally:
        con.close()
    matches.sort(key=lambda m: m["score"], reverse=True)
    return {"path": rel_path, "candidates_scanned": len(candidates), "matches": matches}


def _value_overlap(
    con: duckdb.DuckDBPyConnection, col_a: str, col_b: str
) -> tuple[float, list[Any]]:
    qa, qb = _quote_ident(col_a), _quote_ident(col_b)
    distinct_a = con.execute(
        f"SELECT count(DISTINCT {qa}) FROM t1 WHERE {qa} IS NOT NULL"
    ).fetchone()[0] or 0
    if distinct_a == 0:
        return 0.0, []
    common_rows = con.execute(
        f"SELECT {qa} FROM ("
        f"  SELECT DISTINCT {qa} FROM t1 WHERE {qa} IS NOT NULL"
        f") JOIN ("
        f"  SELECT DISTINCT {qb} AS __b FROM t2 WHERE {qb} IS NOT NULL"
        f") ON {qa} = __b LIMIT 100"
    ).fetchall()
    common_count = len(common_rows)
    # If we hit the cap, query the real count.
    if common_count == 100:
        common_count = con.execute(
            f"SELECT count(*) FROM ("
            f"  SELECT DISTINCT {qa} FROM t1 WHERE {qa} IS NOT NULL"
            f") JOIN ("
            f"  SELECT DISTINCT {qb} AS __b FROM t2 WHERE {qb} IS NOT NULL"
            f") ON {qa} = __b"
        ).fetchone()[0]
    sample = [r[0] for r in common_rows[:3]]
    return common_count / distinct_a, sample


def _list_other_csvs(exclude_path: str) -> list[str]:
    out: list[str] = []
    for entry in vault.list_tree():
        if entry.type != "file":
            continue
        if entry.path == exclude_path:
            continue
        if entry.path.lower().endswith((".csv", ".tsv")):
            out.append(entry.path)
    return out


def _name_similarity(a: str, b: str) -> float:
    na, nb = _normalize(a), _normalize(b)
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.7
    # token overlap
    ta, tb = set(na.split("_")), set(nb.split("_"))
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    if not inter:
        return 0.0
    return len(inter) / max(len(ta), len(tb))


def _normalize(name: str) -> str:
    s = re.sub(r"([a-z])([A-Z])", r"\1_\2", name)
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    # Common id suffixes/prefixes are noise — strip the column "id" trailing token
    # is informative, leave alone.
    return s


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _row_to_dict(columns: list[str], row: tuple) -> dict[str, Any]:
    return {c: row[i] for i, c in enumerate(columns)}


# ---------------------------------------------------------------------------
# CRUD helpers (used by /vault/csv/* endpoints).
#
# We round-trip via the stdlib `csv` module to preserve quoting/escapes — the
# DuckDB engine is read-only-ish and we want full control over the on-disk
# format when we write back. Files above _MAX_EDITABLE_BYTES are rejected.
# ---------------------------------------------------------------------------


def _delim_for(path: Path) -> str:
    return "\t" if path.suffix.lower() == ".tsv" else ","


def _ensure_editable(full: Path) -> None:
    if full.stat().st_size > _MAX_EDITABLE_BYTES:
        raise ValueError(
            f"file too large for UI editing ({full.stat().st_size} bytes; "
            f"max {_MAX_EDITABLE_BYTES}). Use the vault_csv tool for analytics."
        )


def _read_all(full: Path) -> tuple[list[str], list[list[str]]]:
    with full.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter=_delim_for(full))
        try:
            header = next(reader)
        except StopIteration:
            return [], []
        rows = [row for row in reader]
    # Pad short rows so every row has len(header) columns.
    width = len(header)
    rows = [r + [""] * (width - len(r)) if len(r) < width else r[:width] for r in rows]
    return header, rows


def _write_all(full: Path, header: list[str], rows: list[list[str]]) -> None:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, delimiter=_delim_for(full))
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    full.write_text(buf.getvalue(), encoding="utf-8")


def _publish_indexed(rel_path: str) -> None:
    # Notify the UI's vault-event SSE stream so any open CsvEditorView for
    # this file reloads. CSV writes bypass vault.write_file (which would
    # publish on its own), so we post the event explicitly.
    try:
        from .server.event_bus import publish
        publish({"type": "vault.indexed", "path": rel_path})
    except Exception:
        pass


def csv_read_page(
    rel_path: str,
    *,
    offset: int = 0,
    limit: int = 100,
    sort: str | None = None,
    sort_dir: str = "asc",
) -> dict[str, Any]:
    full = _resolve(rel_path)
    header, rows = _read_all(full)
    if sort and sort in header:
        idx = header.index(sort)
        rows.sort(key=lambda r: r[idx], reverse=(sort_dir == "desc"))
    total = len(rows)
    page = rows[offset : offset + limit]
    return {
        "path": rel_path,
        "columns": header,
        "rows": [_row_to_dict(header, tuple(r)) for r in page],
        "total_rows": total,
        "offset": offset,
        "limit": limit,
    }


def csv_append_row(rel_path: str, values: dict[str, Any]) -> dict[str, Any]:
    full = _resolve(rel_path)
    _ensure_editable(full)
    header, rows = _read_all(full)
    new = [str(values.get(c, "")) for c in header]
    rows.append(new)
    _write_all(full, header, rows)
    _publish_indexed(rel_path)
    return {"path": rel_path, "row_index": len(rows) - 1, "total_rows": len(rows)}


def csv_update_cell(rel_path: str, row_index: int, column: str, value: Any) -> dict[str, Any]:
    full = _resolve(rel_path)
    _ensure_editable(full)
    header, rows = _read_all(full)
    if column not in header:
        raise ValueError(f"unknown column: {column!r}")
    if row_index < 0 or row_index >= len(rows):
        raise ValueError(f"row_index out of range: {row_index}")
    rows[row_index][header.index(column)] = "" if value is None else str(value)
    _write_all(full, header, rows)
    _publish_indexed(rel_path)
    return {"path": rel_path, "row_index": row_index, "column": column}


def csv_delete_row(rel_path: str, row_index: int) -> dict[str, Any]:
    full = _resolve(rel_path)
    _ensure_editable(full)
    header, rows = _read_all(full)
    if row_index < 0 or row_index >= len(rows):
        raise ValueError(f"row_index out of range: {row_index}")
    rows.pop(row_index)
    _write_all(full, header, rows)
    _publish_indexed(rel_path)
    return {"path": rel_path, "total_rows": len(rows)}


def csv_set_schema(rel_path: str, columns: list[dict[str, str]]) -> dict[str, Any]:
    """Reorder/rename/add/remove columns.

    Each entry: ``{"name": "<new>", "rename_from": "<old>"?}``.
    Columns not listed are dropped. New columns (no rename_from / not in old
    header) are added with empty values.
    """
    full = _resolve(rel_path)
    _ensure_editable(full)
    header, rows = _read_all(full)
    new_header = [c["name"] for c in columns]
    if len(set(new_header)) != len(new_header):
        raise ValueError("duplicate column names in schema")
    # Build new rows by mapping each new column to its source.
    new_rows: list[list[str]] = []
    for r in rows:
        new_row: list[str] = []
        for c in columns:
            src = c.get("rename_from") or c["name"]
            if src in header:
                new_row.append(r[header.index(src)])
            else:
                new_row.append("")
        new_rows.append(new_row)
    _write_all(full, new_header, new_rows)
    _publish_indexed(rel_path)
    return {"path": rel_path, "columns": new_header, "total_rows": len(new_rows)}
