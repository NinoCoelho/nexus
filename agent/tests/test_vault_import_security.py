"""Security tests for vault_import CSV path handling + DuckDB quoting.

Regression tests for:
  * path traversal via ``csv_path`` against import/batch temp dirs
  * DuckDB SQL injection via quote characters in resolved file paths
  * non-CSV suffix rejection
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from nexus.server.routes import vault_import


@pytest.fixture
def temp_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    temp_dir = tmp_path / "zip"
    (temp_dir / "data").mkdir(parents=True)
    (temp_dir / "data" / "table.csv").write_text("id,name\n1,A\n", encoding="utf-8")
    (temp_dir / "notes.txt").write_text("not a csv", encoding="utf-8")
    import_id = "sec-test"
    monkeypatch.setattr(
        vault_import, "_active_imports",
        {import_id: vault_import._TempImport(
            import_id=import_id, created_at=time.time(), tree=[], temp_dir=str(temp_dir),
            stats={},
        )},
    )
    return temp_dir, import_id


def test_import_id_traversal_blocked(temp_import) -> None:
    _, import_id = temp_import
    with pytest.raises(HTTPException) as exc:
        vault_import._resolve_csv_import_path("../../etc/passwd", import_id, None, "temp")
    assert exc.value.status_code == 404


def test_import_id_sneaky_traversal_blocked(temp_import) -> None:
    _, import_id = temp_import
    for evil in ("data/../../../secret.csv", "./../outside.csv", "a/../../b.csv"):
        with pytest.raises(HTTPException) as exc:
            vault_import._resolve_csv_import_path(evil, import_id, None, "temp")
        assert exc.value.status_code == 404


def test_non_csv_suffix_rejected(temp_import) -> None:
    _, import_id = temp_import
    with pytest.raises(HTTPException) as exc:
        vault_import._resolve_csv_import_path("notes.txt", import_id, None, "temp")
    assert exc.value.status_code == 400


def test_valid_temp_csv_resolves(temp_import) -> None:
    temp_dir, import_id = temp_import
    full = vault_import._resolve_csv_import_path("data/table.csv", import_id, None, "temp")
    assert full == Path(temp_dir) / "data" / "table.csv"


def test_unknown_import_id_404() -> None:
    with pytest.raises(HTTPException) as exc:
        vault_import._resolve_csv_import_path("x.csv", "missing", None, "temp")
    assert exc.value.status_code == 404


def test_batch_id_flattens_and_confines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = tmp_path / "csv-app"
    batch = base / "batch1"
    batch.mkdir(parents=True)
    (batch / "upload_data.csv").write_text("a\n1\n", encoding="utf-8")
    monkeypatch.setattr(vault_import, "_CSV_TEMP_BASE", base)

    full = vault_import._resolve_csv_import_path("upload/data.csv", None, "batch1", "temp")
    assert full == batch / "upload_data.csv"

    # Flattened traversal stays confined (falls back to a csv inside the batch dir)
    fallback = vault_import._resolve_csv_import_path("../other.csv", None, "batch1", "temp")
    assert fallback.parent == batch


def test_vault_source_escape_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexus import vault

    monkeypatch.setattr(vault, "_VAULT_ROOT", Path("/tmp/nexus-vault-root-nonexistent"))
    with pytest.raises(HTTPException) as exc:
        vault_import._resolve_csv_import_path("../../etc/passwd", None, None, "vault")
    assert exc.value.status_code == 400


def test_analyze_apostrophe_filename_via_route(temp_import, monkeypatch: pytest.MonkeyPatch) -> None:
    """A quote in the resolved path must not break out of the DuckDB literal."""
    temp_dir, import_id = temp_import
    evil = temp_dir / "o'brien'); DROP VIEW csv_data;--.csv"
    evil.write_text("id\n1\n", encoding="utf-8")
    rel = evil.name

    async def fake_llm(headers, sample_rows, total_rows, agent, cfg):
        return {"entities": [], "relationships": []}

    monkeypatch.setattr(vault_import, "_analyze_csv_llm", fake_llm)
    app = FastAPI()
    app.include_router(vault_import.router)

    class _State:
        agent = None
        mutable_state: dict = {"cfg": None}

    app.state.agent = None
    app.state.mutable_state = {"cfg": None}

    client = TestClient(app)
    r = client.post("/vault/csv-analyze", json={"csv_path": rel, "import_id": import_id})
    assert r.status_code == 200, r.text
    assert r.json()["csv_stats"]["rows"] == 1
    assert len(evil.read_text()) > 0  # file untouched
