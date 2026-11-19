"""Tests for the external-agent CLI skills: nexus kanban / table commands
and the expanded bootstrap skills list (vault, kanban, tables)."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus.ext_skill import _all_skills

# ── kanban CLI ────────────────────────────────────────────────────────


def test_kanban_boards_json(monkeypatch, capsys):
    from nexus.cli import kanban_cli

    monkeypatch.setattr(
        "nexus.vault_kanban.boards.list_boards",
        lambda: [{"path": "boards/work.md", "title": "Work"}],
    )
    kanban_cli.boards(json_out=True)
    items = json.loads(capsys.readouterr().out)
    assert items[0]["path"] == "boards/work.md"


def test_kanban_move_resolves_card_title(monkeypatch, capsys):
    from nexus.cli import kanban_cli

    monkeypatch.setattr(
        kanban_cli,
        "_resolve_card",
        lambda path, ref: "c-123",
    )

    def fake_invoke(args: dict) -> dict:
        return {"ok": True}

    monkeypatch.setattr(kanban_cli, "_invoke", fake_invoke)
    kanban_cli.move(path="boards/work.md", card="Fix login", lane="done")
    assert capsys.readouterr().out.strip() == "Moved Fix login → done."


def test_kanban_error_exits_1(monkeypatch):
    import typer

    from nexus.cli import kanban_cli

    monkeypatch.setattr(kanban_cli, "_resolve_card", lambda path, ref: "x")
    monkeypatch.setattr(kanban_cli, "_invoke", lambda args: {"ok": False, "error": "board not found"})
    with pytest.raises(typer.Exit) as exc:
        kanban_cli.move(path="boards/nope.md", card="x", lane="y")
    assert exc.value.exit_code == 1


# ── table CLI ─────────────────────────────────────────────────────────


def test_table_sql_passes_query(monkeypatch, capsys):
    from nexus.cli import table_cli

    captured: dict = {}

    def fake_invoke(args: dict) -> dict:
        captured.update(args)
        return {"ok": True, "results": [{"status": "paid", "n": 3}]}

    monkeypatch.setattr(table_cli, "_invoke", fake_invoke)
    table_cli.sql(path="data/orders.md", query="SELECT 1", json_out=True)
    doc = json.loads(capsys.readouterr().out)
    assert captured["action"] == "query"
    assert captured["sql"] == "SELECT 1"
    assert doc["results"][0]["n"] == 3


def test_table_add_invalid_json_exits_2(monkeypatch):
    import typer

    from nexus.cli import table_cli

    with pytest.raises(typer.Exit) as exc:
        table_cli.add(path="data/orders.md", row="{not json", json_out=True)
    assert exc.value.exit_code == 2


def test_table_update_uses_row_arg(monkeypatch, capsys):
    from nexus.cli import table_cli

    captured: dict = {}

    def fake_invoke(args: dict) -> dict:
        captured.update(args)
        return {"ok": True}

    monkeypatch.setattr(table_cli, "_invoke", fake_invoke)
    table_cli.update(path="data/orders.md", row_id="r1", patch='{"status":"paid"}', json_out=True)
    assert captured["action"] == "update_row"
    assert captured["row"] == {"status": "paid"}  # handler expects `row`, not `patch`
    assert "row" not in captured or captured.get("row_id") == "r1"


# ── bootstrap skills list ─────────────────────────────────────────────


def test_all_skills_contains_expected_four():
    skills = _all_skills()
    for name in ("nexus-delegation", "nexus-vault", "nexus-kanban", "nexus-tables"):
        assert name in skills, f"missing skill: {name}"
        assert skills[name].startswith("---\n"), f"{name} lacks frontmatter"
        assert "Self-install" in skills[name], f"{name} lacks self-install steps"


def test_vault_skill_documents_special_formats():
    vault = _all_skills()["nexus-vault"]
    for needle in ("kanban-plugin", "data-table-plugin", "workflow-plugin", "nexus table", "nexus kanban"):
        assert needle in vault, f"vault skill missing: {needle}"


def test_tables_skill_documents_sql_and_import():
    tables = _all_skills()["nexus-tables"]
    for needle in ("DuckDB", "FROM t", "import-csv", "--dry-run", "GROUP BY"):
        assert needle in tables, f"tables skill missing: {needle}"


def test_kanban_skill_documents_flows():
    kanban = _all_skills()["nexus-kanban"]
    for needle in ("nexus kanban boards", "nexus kanban add", "nexus kanban move", "--json"):
        assert needle in kanban, f"kanban skill missing: {needle}"


def test_bootstrap_lists_all_skills(client_bootstrap: TestClient):
    body = client_bootstrap.get("/bootstrap").text
    for name in ("nexus-delegation", "nexus-vault", "nexus-kanban", "nexus-tables"):
        assert f"/bootstrap/skills/{name}.md" in body, f"bootstrap doc missing link: {name}"


def test_bootstrap_serves_kanban_and_tables_skills(client_bootstrap: TestClient):
    for name, needle in (("nexus-kanban", "nexus kanban boards"), ("nexus-tables", "DuckDB"), ("nexus-vault", "kanban-plugin")):
        r = client_bootstrap.get(f"/bootstrap/skills/{name}.md")
        assert r.status_code == 200
        assert needle in r.text


# ── shared fixture ────────────────────────────────────────────────────


@pytest.fixture
def client_bootstrap(monkeypatch) -> TestClient:
    from nexus.server.routes import bootstrap

    monkeypatch.setattr(bootstrap, "_bot_roster", list)

    app = FastAPI()
    app.include_router(bootstrap.router)
    return TestClient(app)
