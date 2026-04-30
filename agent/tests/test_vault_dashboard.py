"""Tests for vault_dashboard parser, lazy default, and CRUD."""

from __future__ import annotations

from pathlib import Path

import pytest

import nexus.vault as vault_module
from nexus import vault_dashboard, vault_datatable


@pytest.fixture(autouse=True)
def _vault_tmp(tmp_path: Path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    monkeypatch.setattr(vault_module, "_VAULT_ROOT", vault_root)
    return vault_root


# ── Lazy default ─────────────────────────────────────────────────────────────


def test_default_dashboard_for_missing_file():
    """GET on a folder without `_data.md` returns sensible defaults."""
    d = vault_dashboard.read_dashboard("shop")
    assert d["folder"] == "shop"
    assert d["title"] == "shop"
    assert d["chat_session_id"] is None
    assert d["operations"] == []
    assert d["exists"] is False


def test_default_dashboard_does_not_write(tmp_path: Path, _vault_tmp):
    """Reading a missing dashboard must not materialize the file."""
    vault_dashboard.read_dashboard("shop")
    assert not (_vault_tmp / "shop" / "_data.md").exists()


def test_default_dashboard_root_folder():
    d = vault_dashboard.read_dashboard("")
    assert d["folder"] == ""
    assert d["title"] == "(root)"


# ── Round-trip ───────────────────────────────────────────────────────────────


def test_write_then_read_dashboard():
    written = vault_dashboard.write_dashboard("shop", {
        "folder": "shop",
        "title": "Webshop",
        "chat_session_id": None,
        "operations": [
            {"id": "op_add_customer", "label": "Add customer", "kind": "chat",
             "prompt": "Add a new customer.", "order": 0},
            {"id": "op_quick_order", "label": "Quick add order", "kind": "form",
             "table": "./orders.md", "order": 1},
        ],
    })
    assert written["exists"] is True
    assert written["title"] == "Webshop"
    assert len(written["operations"]) == 2

    read = vault_dashboard.read_dashboard("shop")
    assert read["exists"] is True
    assert read["title"] == "Webshop"
    assert [op["id"] for op in read["operations"]] == ["op_add_customer", "op_quick_order"]


def test_read_preserves_operation_order():
    vault_dashboard.write_dashboard("shop", {
        "folder": "shop",
        "operations": [
            {"id": "b", "label": "B", "kind": "chat", "prompt": "", "order": 2},
            {"id": "a", "label": "A", "kind": "chat", "prompt": "", "order": 0},
            {"id": "c", "label": "C", "kind": "chat", "prompt": "", "order": 1},
        ],
    })
    read = vault_dashboard.read_dashboard("shop")
    assert [op["id"] for op in read["operations"]] == ["a", "c", "b"]


def test_is_dashboard_file_true():
    content = "---\ndata-dashboard: basic\n---\n\n## Dashboard\n```yaml\noperations: []\n```\n"
    assert vault_dashboard.is_dashboard_file(content) is True


def test_is_dashboard_file_false_other_plugin():
    content = "---\nkanban-plugin: basic\n---\n"
    assert vault_dashboard.is_dashboard_file(content) is False


# ── Operations CRUD ──────────────────────────────────────────────────────────


def test_upsert_operation_appends_first_op():
    d = vault_dashboard.upsert_operation("shop", {
        "id": "op_add_customer", "label": "Add customer", "kind": "chat",
        "prompt": "Add a new customer.",
    })
    assert len(d["operations"]) == 1
    assert d["operations"][0]["id"] == "op_add_customer"
    assert d["operations"][0]["order"] == 0


def test_upsert_operation_replaces_by_id():
    vault_dashboard.upsert_operation("shop", {
        "id": "op_add_customer", "label": "Add customer", "kind": "chat", "prompt": "v1",
    })
    vault_dashboard.upsert_operation("shop", {
        "id": "op_add_customer", "label": "Add customer", "kind": "chat", "prompt": "v2",
    })
    d = vault_dashboard.read_dashboard("shop")
    assert len(d["operations"]) == 1
    assert d["operations"][0]["prompt"] == "v2"


def test_upsert_operation_form_requires_table():
    with pytest.raises(ValueError, match="table"):
        vault_dashboard.upsert_operation("shop", {
            "id": "op_form", "label": "Form op", "kind": "form", "prompt": "",
        })


def test_upsert_operation_rejects_bad_id():
    with pytest.raises(ValueError, match="slug"):
        vault_dashboard.upsert_operation("shop", {
            "id": "Bad ID With Spaces", "label": "x", "kind": "chat", "prompt": "",
        })


def test_delete_operation_removes_by_id():
    vault_dashboard.upsert_operation("shop", {"id": "a", "label": "A", "kind": "chat", "prompt": ""})
    vault_dashboard.upsert_operation("shop", {"id": "b", "label": "B", "kind": "chat", "prompt": ""})
    d = vault_dashboard.delete_operation("shop", "a")
    assert [op["id"] for op in d["operations"]] == ["b"]


def test_set_chat_session_persists():
    d = vault_dashboard.set_chat_session("shop", "01HXY")
    assert d["chat_session_id"] == "01HXY"
    assert vault_dashboard.read_dashboard("shop")["chat_session_id"] == "01HXY"


def test_set_chat_session_clears_with_none():
    vault_dashboard.set_chat_session("shop", "01HXY")
    vault_dashboard.set_chat_session("shop", None)
    assert vault_dashboard.read_dashboard("shop")["chat_session_id"] is None


# ── Delete database ──────────────────────────────────────────────────────────


def test_delete_database_removes_files(_vault_tmp):
    vault_datatable.create_table("shop/customers.md", {"fields": [{"name": "id", "kind": "text"}]})
    vault_datatable.create_table("shop/orders.md", {"fields": [{"name": "id", "kind": "text"}]})
    vault_dashboard.set_chat_session("shop", "session-id")

    res = vault_dashboard.delete_database("shop", confirm="shop")
    assert res["folder"] == "shop"
    assert res["deleted"] >= 3  # 2 tables + 1 _data.md
    assert not (_vault_tmp / "shop").exists()


def test_delete_database_confirm_mismatch_raises():
    vault_datatable.create_table("shop/customers.md", {"fields": [{"name": "id", "kind": "text"}]})
    with pytest.raises(ValueError, match="confirm"):
        vault_dashboard.delete_database("shop", confirm="not-shop")


def test_delete_database_root_rejected():
    with pytest.raises(ValueError, match="root"):
        vault_dashboard.delete_database("", confirm="")


def test_delete_database_missing_folder_raises():
    with pytest.raises(FileNotFoundError):
        vault_dashboard.delete_database("nope", confirm="nope")
