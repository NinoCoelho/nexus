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


def test_datatable_md_redirects_to_datatable_manage(isolated_vault: Path) -> None:
    """A `.md` file with `data-table-plugin: basic` frontmatter should not be
    treated as a CSV — return a structured hint pointing the agent at
    `datatable_manage` instead of the bare 'not a CSV/TSV file' error."""
    (isolated_vault / "patients.md").write_text(
        "---\ndata-table-plugin: basic\n---\n\n"
        "## Schema\n```yaml\nfields:\n  - {name: name, kind: text}\n```\n\n"
        "## Rows\n```yaml\n[]\n```\n",
        encoding="utf-8",
    )
    out = _call({"action": "schema", "path": "patients.md"})
    assert out["ok"] is False
    assert "datatable" in out["error"].lower()
    assert out["hint"]["tool"] == "datatable_manage"
    assert "view" in out["hint"]["suggested_actions"]


def test_plain_md_passes_through_to_csv_validator(isolated_vault: Path) -> None:
    """A `.md` file *without* the datatable frontmatter still falls through to
    the regular extension check (so the user gets the existing 'not a CSV/TSV
    file' error rather than a misleading datatable hint)."""
    (isolated_vault / "notes.md").write_text("# just a doc\n", encoding="utf-8")
    out = _call({"action": "schema", "path": "notes.md"})
    assert out["ok"] is False
    assert "CSV" in out["error"] or "csv" in out["error"]
    assert "hint" not in out
