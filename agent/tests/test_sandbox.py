"""Tests for the restricted Python sandbox (_sandbox.py)."""

from __future__ import annotations

from nexus.tools._sandbox import run_sandbox


def test_run_sandbox_basic_output():
    result = run_sandbox("print('hello world')", {"rows": []})
    assert result["ok"] is True
    assert "hello world" in result["output"]


def test_run_sandbox_captures_multiline():
    script = "for i in range(3):\n    print(f'line {i}')"
    result = run_sandbox(script, {"rows": []})
    assert result["ok"] is True
    assert "line 0" in result["output"]
    assert "line 1" in result["output"]
    assert "line 2" in result["output"]


def test_run_sandbox_error():
    result = run_sandbox("1 / 0", {"rows": []})
    assert result["ok"] is False
    assert result.get("exit_code") != 0
    assert result.get("error")


def test_run_sandbox_timeout():
    result = run_sandbox(
        "x = 0\nwhile True:\n    x += 1",
        {"rows": []},
        timeout=2,
    )
    assert result["ok"] is False
    assert "timed out" in result["error"].lower()


def test_run_sandbox_dataframe():
    rows = [
        {"name": "Alice", "age": 30},
        {"name": "Bob", "age": 25},
    ]
    result = run_sandbox(
        "if pd is not None:\n    print(t.shape)\nelse:\n    print(len(t))",
        {"rows": rows},
    )
    assert result["ok"] is True
    assert "2" in result["output"]


def test_run_sandbox_pandas_or_list():
    rows = [{"x": 1}, {"x": 2}]
    result = run_sandbox("print(type(t).__name__)", {"rows": rows})
    assert result["ok"] is True
    name = result["output"].strip().split("\n")[-1]
    assert name in ("DataFrame", "list")


def test_run_sandbox_duckdb_available():
    script = "print(type(duckdb.connect()))"
    result = run_sandbox(script, {"rows": []})
    assert result["ok"] is True
    assert "DuckDBPyConnection" in result["output"]


def test_run_sandbox_numpy_available():
    script = "print(np.array([1, 2, 3]).sum())"
    result = run_sandbox(script, {"rows": []})
    assert result["ok"] is True
    assert "6" in result["output"]


def test_run_sandbox_assert_row_count_pass():
    rows = [{"x": i} for i in range(5)]
    result = run_sandbox("assert_row_count(min=1, max=10)", {"rows": rows})
    assert "PASS assert_row_count" in result["output"]


def test_run_sandbox_assert_row_count_fail():
    rows = [{"x": i} for i in range(5)]
    result = run_sandbox("assert_row_count(max=3)", {"rows": rows})
    assert "FAIL assert_row_count" in result["output"]


def test_run_sandbox_assert_no_nulls():
    rows = [{"a": 1, "b": None}, {"a": 2, "b": "x"}]
    result = run_sandbox("assert_no_nulls(columns=['a', 'b'])", {"rows": rows})
    assert "PASS assert_no_nulls(a)" in result["output"]
    assert "FAIL assert_no_nulls(b)" in result["output"]


def test_run_sandbox_assert_unique():
    rows = [{"id": 1}, {"id": 1}, {"id": 2}]
    result = run_sandbox("assert_unique('id')", {"rows": rows})
    assert "FAIL assert_unique(id)" in result["output"]
    assert "duplicates" in result["output"]


def test_run_sandbox_assert_range():
    rows = [{"val": 10}, {"val": 20}, {"val": 30}]
    result = run_sandbox("assert_range('val', min=0, max=50)", {"rows": rows})
    assert "PASS assert_range(val)" in result["output"]


def test_run_sandbox_assert_range_fail():
    rows = [{"val": 10}, {"val": 99}]
    result = run_sandbox("assert_range('val', max=50)", {"rows": rows})
    assert "FAIL assert_range(val)" in result["output"]


def test_run_sandbox_assert_full_coverage_pass():
    result = run_sandbox(
        "assert_full_coverage()",
        {"rows": [{"x": 1}], "row_count": 1, "row_count_loaded": 1, "truncated": False},
    )
    assert "PASS assert_full_coverage" in result["output"]


def test_run_sandbox_assert_full_coverage_warn():
    result = run_sandbox(
        "assert_full_coverage()",
        {"rows": [{"x": 1}], "row_count": 20000, "row_count_loaded": 10000, "truncated": True},
    )
    assert "WARN assert_full_coverage" in result["output"]
    assert "truncated" in result["output"]


def test_run_sandbox_output_cap():
    script = "print('x' * 10000)"
    result = run_sandbox(script, {"rows": []})
    assert result["ok"] is True
    assert len(result["output"]) <= 5000  # cap + truncation notice
    assert "truncated" in result["output"].lower()


def test_run_sandbox_restricted_open():
    result = run_sandbox("open('/etc/passwd', 'r')", {"rows": []})
    assert result["ok"] is False
    assert result.get("error") or result.get("exit_code")


def test_run_sandbox_restricted_import():
    result = run_sandbox("import os", {"rows": []})
    assert result["ok"] is False
    assert "__import__" in result.get("error", "") or "ImportError" in result.get("error", "")


def test_run_sandbox_context_field_specs():
    ctx = {
        "rows": [{"a": 1}],
        "field_specs": [{"name": "a", "type": "BIGINT"}],
    }
    result = run_sandbox("print(_schema_fields)", ctx)
    assert result["ok"] is True
    assert "a" in result["output"]
