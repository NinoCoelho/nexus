"""vault_csv tool dispatcher."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus import vault
from nexus.tools.csv_tool import handle_csv_tool


@pytest.fixture
def isolated_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(vault, "_VAULT_ROOT", tmp_path)
    (tmp_path / "data.csv").write_text(
        "id,name,score\n1,Alice,90\n2,Bob,75\n3,Carol,82\n",
        encoding="utf-8",
    )
    return tmp_path


def _call(args: dict) -> dict:
    return json.loads(handle_csv_tool(args))


def test_action_required() -> None:
    out = _call({"path": "data.csv"})
    assert out["ok"] is False
    assert "action" in out["error"]


def test_path_required() -> None:
    out = _call({"action": "schema"})
    assert out["ok"] is False
    assert "path" in out["error"]


def test_schema(isolated_vault: Path) -> None:
    out = _call({"action": "schema", "path": "data.csv"})
    assert out["ok"]
    assert out["row_count"] == 3


def test_sample(isolated_vault: Path) -> None:
    out = _call({"action": "sample", "path": "data.csv", "n": 2})
    assert out["ok"]
    assert len(out["rows"]) == 2


def test_describe(isolated_vault: Path) -> None:
    out = _call({"action": "describe", "path": "data.csv", "columns": ["score"]})
    assert out["ok"]
    assert out["stats"][0]["column"] == "score"
    assert out["stats"][0]["min"] == 75


def test_query_requires_sql(isolated_vault: Path) -> None:
    out = _call({"action": "query", "path": "data.csv"})
    assert out["ok"] is False


def test_query_columnar_default(isolated_vault: Path) -> None:
    out = _call({"action": "query", "path": "data.csv", "sql": "SELECT count(*) AS n FROM t"})
    assert out["ok"]
    assert out["format"] == "columns"
    assert out["columns"] == ["n"]
    assert out["data"] == [[3]]


def test_query_rows_format(isolated_vault: Path) -> None:
    out = _call({
        "action": "query",
        "path": "data.csv",
        "sql": "SELECT count(*) AS n FROM t",
        "format": "rows",
    })
    assert out["ok"]
    assert out["format"] == "rows"
    assert out["rows"][0]["n"] == 3


def test_query_rejects_mutation(isolated_vault: Path) -> None:
    out = _call({"action": "query", "path": "data.csv", "sql": "DELETE FROM t"})
    assert out["ok"] is False
    assert "SELECT" in out["error"]


def test_unknown_action(isolated_vault: Path) -> None:
    out = _call({"action": "burninate", "path": "data.csv"})
    assert out["ok"] is False
