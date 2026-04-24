"""Tests for vault_datatable parse/write round-trip and datatable_tool actions."""

from __future__ import annotations

from pathlib import Path

import pytest

import nexus.vault as vault_module
from nexus import vault_datatable
from nexus.tools.datatable_tool import handle_datatable_tool
import json


# ── Fixture: point vault at a temp directory ─────────────────────────────────


@pytest.fixture(autouse=True)
def _vault_tmp(tmp_path: Path, monkeypatch):
    """Redirect vault operations to a temp directory for isolation."""
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    monkeypatch.setattr(vault_module, "_VAULT_ROOT", vault_root)
    return vault_root


# ── is_datatable_file ─────────────────────────────────────────────────────────


def test_is_datatable_file_true():
    content = "---\ndata-table-plugin: basic\n---\n\n## Schema\n```yaml\ntitle: Test\n```\n"
    assert vault_datatable.is_datatable_file(content) is True


def test_is_datatable_file_false_kanban():
    content = "---\nkanban-plugin: basic\n---\n"
    assert vault_datatable.is_datatable_file(content) is False


def test_is_datatable_file_false_no_frontmatter():
    content = "# Just a plain doc\n"
    assert vault_datatable.is_datatable_file(content) is False


# ── create_table + read_table round-trip ─────────────────────────────────────


def test_create_and_read_table(tmp_path: Path):
    schema = {
        "title": "Bug triage",
        "fields": [
            {"name": "id", "kind": "text", "required": True},
            {"name": "severity", "kind": "select", "choices": ["low", "med", "high"]},
            {"name": "status", "kind": "select", "choices": ["open", "done"]},
        ],
    }
    vault_datatable.create_table("bugs.md", schema)
    tbl = vault_datatable.read_table("bugs.md")
    assert tbl["schema"]["title"] == "Bug triage"
    assert len(tbl["schema"]["fields"]) == 3
    assert tbl["rows"] == []


def test_add_row_and_read_back():
    schema = {"fields": [{"name": "name", "kind": "text"}]}
    vault_datatable.create_table("people.md", schema)
    row = vault_datatable.add_row("people.md", {"name": "Alice"})
    assert "_id" in row
    assert row["name"] == "Alice"
    tbl = vault_datatable.read_table("people.md")
    assert len(tbl["rows"]) == 1
    assert tbl["rows"][0]["name"] == "Alice"


def test_add_multiple_rows():
    schema = {"fields": [{"name": "val", "kind": "number"}]}
    vault_datatable.create_table("nums.md", schema)
    for i in range(3):
        vault_datatable.add_row("nums.md", {"val": i})
    tbl = vault_datatable.read_table("nums.md")
    assert len(tbl["rows"]) == 3


def test_update_row():
    schema = {"fields": [{"name": "status", "kind": "text"}]}
    vault_datatable.create_table("tasks.md", schema)
    row = vault_datatable.add_row("tasks.md", {"status": "open"})
    row_id = row["_id"]
    updated = vault_datatable.update_row("tasks.md", row_id, {"status": "done"})
    assert updated["status"] == "done"
    tbl = vault_datatable.read_table("tasks.md")
    assert tbl["rows"][0]["status"] == "done"


def test_update_row_not_found():
    schema = {"fields": [{"name": "x", "kind": "text"}]}
    vault_datatable.create_table("x.md", schema)
    with pytest.raises(KeyError, match="not found"):
        vault_datatable.update_row("x.md", "nonexistent", {"x": "y"})


def test_delete_row():
    schema = {"fields": [{"name": "name", "kind": "text"}]}
    vault_datatable.create_table("del.md", schema)
    r1 = vault_datatable.add_row("del.md", {"name": "A"})
    vault_datatable.add_row("del.md", {"name": "B"})
    vault_datatable.delete_row("del.md", r1["_id"])
    tbl = vault_datatable.read_table("del.md")
    assert len(tbl["rows"]) == 1
    assert tbl["rows"][0]["name"] == "B"


def test_delete_row_not_found():
    schema = {"fields": [{"name": "x", "kind": "text"}]}
    vault_datatable.create_table("dx.md", schema)
    with pytest.raises(KeyError, match="not found"):
        vault_datatable.delete_row("dx.md", "ghost")


def test_set_schema_preserves_rows():
    schema = {"fields": [{"name": "a", "kind": "text"}]}
    vault_datatable.create_table("s.md", schema)
    vault_datatable.add_row("s.md", {"a": "val"})
    new_schema = {"fields": [{"name": "a", "kind": "text"}, {"name": "b", "kind": "number"}]}
    tbl = vault_datatable.set_schema("s.md", new_schema)
    assert len(tbl["schema"]["fields"]) == 2
    assert len(tbl["rows"]) == 1


def test_add_rows_bulk():
    schema = {"fields": [{"name": "n", "kind": "number"}]}
    vault_datatable.create_table("bulk.md", schema)
    added = vault_datatable.add_rows("bulk.md", [{"n": 1}, {"n": 2}, {"n": 3}])
    assert len(added) == 3
    assert all("_id" in r for r in added)
    tbl = vault_datatable.read_table("bulk.md")
    assert [r["n"] for r in tbl["rows"]] == [1, 2, 3]


def test_set_views_round_trip():
    schema = {"fields": [{"name": "x", "kind": "text"}]}
    vault_datatable.create_table("v.md", schema)
    views = [
        {"name": "Open", "filter": "open", "sort": {"field": "x", "dir": "asc"}},
        {"name": "All hidden", "hidden": ["x"]},
    ]
    vault_datatable.set_views("v.md", views)
    tbl = vault_datatable.read_table("v.md")
    assert len(tbl["views"]) == 2
    assert tbl["views"][0]["name"] == "Open"
    assert tbl["views"][0]["sort"] == {"field": "x", "dir": "asc"}


def test_views_preserved_on_row_mutation():
    schema = {"fields": [{"name": "x", "kind": "text"}]}
    vault_datatable.create_table("v2.md", schema)
    vault_datatable.set_views("v2.md", [{"name": "A"}])
    vault_datatable.add_row("v2.md", {"x": "hi"})
    tbl = vault_datatable.read_table("v2.md")
    assert len(tbl["views"]) == 1
    assert tbl["views"][0]["name"] == "A"


def test_round_trip_preserves_special_chars():
    schema = {"fields": [{"name": "note", "kind": "text"}]}
    vault_datatable.create_table("notes.md", schema)
    vault_datatable.add_row("notes.md", {"note": "line1\nline2"})
    tbl = vault_datatable.read_table("notes.md")
    assert "line1" in tbl["rows"][0]["note"]


# ── datatable_tool ─────────────────────────────────────────────────────────────


def test_tool_create_table():
    result = json.loads(handle_datatable_tool({
        "action": "create_table",
        "path": "tool_test.md",
        "schema": {"fields": [{"name": "id", "kind": "text"}]},
    }))
    assert result["ok"] is True
    assert result["path"] == "tool_test.md"


def test_tool_add_and_list_rows():
    handle_datatable_tool({
        "action": "create_table",
        "path": "tl.md",
        "schema": {"fields": [{"name": "x", "kind": "text"}]},
    })
    handle_datatable_tool({
        "action": "add_row",
        "path": "tl.md",
        "row": {"x": "hello"},
    })
    result = json.loads(handle_datatable_tool({"action": "list_rows", "path": "tl.md"}))
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["rows"][0]["x"] == "hello"


def test_tool_update_row():
    handle_datatable_tool({
        "action": "create_table",
        "path": "tu.md",
        "schema": {"fields": [{"name": "s", "kind": "text"}]},
    })
    add_res = json.loads(handle_datatable_tool({
        "action": "add_row",
        "path": "tu.md",
        "row": {"s": "open"},
    }))
    row_id = add_res["row"]["_id"]
    upd_res = json.loads(handle_datatable_tool({
        "action": "update_row",
        "path": "tu.md",
        "row_id": row_id,
        "row": {"s": "closed"},
    }))
    assert upd_res["ok"] is True
    assert upd_res["row"]["s"] == "closed"


def test_tool_delete_row():
    handle_datatable_tool({
        "action": "create_table",
        "path": "td.md",
        "schema": {"fields": [{"name": "v", "kind": "text"}]},
    })
    add_res = json.loads(handle_datatable_tool({
        "action": "add_row",
        "path": "td.md",
        "row": {"v": "bye"},
    }))
    row_id = add_res["row"]["_id"]
    del_res = json.loads(handle_datatable_tool({
        "action": "delete_row",
        "path": "td.md",
        "row_id": row_id,
    }))
    assert del_res["ok"] is True
    list_res = json.loads(handle_datatable_tool({"action": "list_rows", "path": "td.md"}))
    assert list_res["count"] == 0


def test_tool_missing_path():
    result = json.loads(handle_datatable_tool({"action": "create_table", "path": ""}))
    assert result["ok"] is False
    assert "path" in result["error"]


def test_tool_unknown_action():
    result = json.loads(handle_datatable_tool({"action": "explode", "path": "x.md"}))
    assert result["ok"] is False


# ── ask_user form-kind validation ─────────────────────────────────────────────


def test_ask_user_form_kind_validation():
    """Validate that form kind requires a non-empty fields array."""
    from nexus.agent.ask_user_tool import AskUserHandler
    import asyncio

    handler = AskUserHandler(session_store=None)

    async def _run():
        return await handler.invoke({"prompt": "Fill this", "kind": "form"})

    result = asyncio.get_event_loop().run_until_complete(_run())
    # session_store is None, but fields validation happens before that check
    assert result.ok is False
    assert "fields" in (result.error or "")


def test_ask_user_form_kind_invalid_field():
    """Pydantic validation catches bad field kind."""
    from nexus.agent.ask_user_tool import AskUserHandler
    import asyncio

    handler = AskUserHandler(session_store=None)

    async def _run():
        return await handler.invoke({
            "prompt": "Fill this",
            "kind": "form",
            "fields": [{"name": "x", "kind": "INVALID_KIND"}],
        })

    result = asyncio.get_event_loop().run_until_complete(_run())
    assert result.ok is False
    assert result.error is not None
