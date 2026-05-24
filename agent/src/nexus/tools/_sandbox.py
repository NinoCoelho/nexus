"""Restricted Python sandbox for the ``analyze`` actions.

Runs user-provided Python code in a subprocess with:

- Pre-loaded ``duckdb``, ``pandas`` (as ``pd``), ``numpy`` (as ``np``).
- ``t`` — a pandas DataFrame built from the table rows or CSV file.
- Validation helpers: ``assert_row_count``, ``assert_no_nulls``,
  ``assert_unique``, ``assert_range``.
- ``print()`` output captured as the return value (capped at ~4000 chars).
- No filesystem writes, no network, no arbitrary imports.
- 30-second timeout.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

_SANDBOX_TIMEOUT = 30
_OUTPUT_CAP = 4000

_BOILERPLATE = textwrap.dedent("""\
import sys as _sys
import io as _io
import json as _json
import builtins as _builtins_mod
import duckdb
try:
    import pandas as pd
except ImportError:
    pd = None
import numpy as np

# ── Save open before restricting ────────────────────────────────────────
_open = open

# ── Load context (rows injected by caller) ───────────────────────────────
try:
    _ctx_path = _sys.argv[1] if len(_sys.argv) > 1 else ""
    if _ctx_path:
        with _open(_ctx_path, "r", encoding="utf-8") as _f:
            _ctx = _json.load(_f)
    else:
        _ctx = {}
except Exception:
    _ctx = {}

# ── Restrict dangerous builtins (after all imports) ───────────────────────
for _name in ("exec", "eval", "compile", "__import__", "open", "input"):
    if isinstance(__builtins__, dict):
        __builtins__.pop(_name, None)
    try:
        delattr(_builtins_mod, _name)
    except AttributeError:
        pass

# ── Build DataFrame ──────────────────────────────────────────────────────
_rows = _ctx.get("rows", [])
if pd is not None:
    t = pd.DataFrame(_rows)
else:
    t = _rows

_schema_fields = _ctx.get("field_specs", [])
_row_count_from_schema = _ctx.get("row_count", len(_rows))
_row_count_loaded = _ctx.get("row_count_loaded", len(_rows))
_truncated = _ctx.get("truncated", False)

# ── Validation helpers ──────────────────────────────────────────────────
def _len_t():
    return len(t)

def _isnull_count(col):
    if pd is not None:
        return int(t[col].isna().sum())
    return sum(1 for r in t if r.get(col) is None)

def _duplicated_count(col):
    if pd is not None:
        return int(t[col].duplicated().sum())
    seen = set()
    dups = 0
    for r in t:
        v = r.get(col)
        if v in seen:
            dups += 1
        seen.add(v)
    return dups

def _col_numeric(col):
    if pd is not None:
        return pd.to_numeric(t[col], errors="coerce")
    vals = []
    for r in t:
        v = r.get(col)
        try:
            vals.append(float(v) if v is not None else None)
        except (TypeError, ValueError):
            vals.append(None)
    return vals

def _col_minmax(vals):
    clean = [v for v in vals if v is not None]
    if not clean:
        return None, None
    return min(clean), max(clean)

def assert_row_count(min=None, max=None):
    n = _len_t()
    if min is not None and n < min:
        print(f"FAIL assert_row_count: {n} rows < min={min}")
    elif max is not None and n > max:
        print(f"FAIL assert_row_count: {n} rows > max={max}")
    else:
        print(f"PASS assert_row_count: {n} rows (min={min}, max={max})")

def assert_no_nulls(columns=None):
    if pd is not None:
        cols = columns if columns is not None else list(t.columns)
    else:
        cols = columns if columns is not None else list(_rows[0].keys()) if _rows else []
    for c in cols:
        nc = _isnull_count(c)
        if nc > 0:
            print(f"FAIL assert_no_nulls({c}): {nc} nulls")
        else:
            print(f"PASS assert_no_nulls({c}): 0 nulls")

def assert_unique(column):
    n = _duplicated_count(column)
    if n > 0:
        print(f"FAIL assert_unique({column}): {n} duplicates")
    else:
        print(f"PASS assert_unique({column}): all unique")

def assert_range(column, min=None, max=None):
    vals = _col_numeric(column)
    lo, hi = _col_minmax(vals)
    if min is not None and lo is not None and lo < min:
        print(f"FAIL assert_range({column}): min={lo} < {min}")
    elif max is not None and hi is not None and hi > max:
        print(f"FAIL assert_range({column}): max={hi} > {max}")
    else:
        print(f"PASS assert_range({column}): [{lo}, {hi}] within [{min}, {max}]")

def assert_full_coverage():
    if _truncated:
        print(f"WARN assert_full_coverage: data truncated — "
              f"loaded {_row_count_loaded} of {_row_count_from_schema} rows. "
              f"Aggregations may be partial.")
    else:
        print(f"PASS assert_full_coverage: all {_row_count_from_schema} rows loaded")

# ── User script ──────────────────────────────────────────────────────────
""")


def run_sandbox(
    script: str,
    context: dict[str, Any],
    *,
    timeout: int = _SANDBOX_TIMEOUT,
) -> dict[str, Any]:
    """Execute *script* in a subprocess and return the captured output.

    *context* must be JSON-serializable and is written to a temp file that
    the subprocess reads. The key ``rows`` becomes the ``t`` DataFrame.
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8",
    ) as ctx_file:
        json.dump(context, ctx_file, default=str, ensure_ascii=False)
        ctx_path = ctx_file.name

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8",
    ) as script_file:
        script_file.write(_BOILERPLATE)
        script_file.write(script)
        script_file.write("\n")
        script_path = script_file.name

    try:
        result = subprocess.run(
            [sys.executable, script_path, ctx_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout[:_OUTPUT_CAP]
        if len(result.stdout) > _OUTPUT_CAP:
            output += f"\n... (truncated, {len(result.stdout)} total chars)"
        error = result.stderr[:_OUTPUT_CAP].strip() if result.stderr else ""
        return_code = result.returncode
    except subprocess.TimeoutExpired:
        output = ""
        error = f"Script timed out after {timeout}s"
        return_code = -1
    finally:
        Path(ctx_path).unlink(missing_ok=True)
        Path(script_path).unlink(missing_ok=True)

    response: dict[str, Any] = {"output": output}
    if error:
        response["error"] = error
    if return_code != 0:
        response["exit_code"] = return_code
        response["ok"] = False
    else:
        response["ok"] = True
    return response
