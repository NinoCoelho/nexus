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


# ── kind: ref relations ──────────────────────────────────────────────────────


def test_kind_ref_round_trip():
    """Schema fields with kind: ref preserve target_table + cardinality."""
    schema = {
        "fields": [
            {"name": "id", "kind": "text"},
            {
                "name": "project",
                "kind": "ref",
                "target_table": "../projects/projects.md",
                "cardinality": "one",
            },
            {
                "name": "tags",
                "kind": "ref",
                "target_table": "tags.md",
                "cardinality": "many",
            },
        ],
    }
    vault_datatable.create_table("data/tasks.md", schema)
    vault_datatable.add_row(
        "data/tasks.md",
        {"id": "T-1", "project": "P-42", "tags": ["urgent", "bug"]},
    )
    tbl = vault_datatable.read_table("data/tasks.md")
    fields = tbl["schema"]["fields"]
    project_field = next(f for f in fields if f["name"] == "project")
    assert project_field["kind"] == "ref"
    assert project_field["target_table"] == "../projects/projects.md"
    assert project_field["cardinality"] == "one"
    tags_field = next(f for f in fields if f["name"] == "tags")
    assert tags_field["cardinality"] == "many"
    assert tbl["rows"][0]["project"] == "P-42"
    assert tbl["rows"][0]["tags"] == ["urgent", "bug"]


def test_resolve_ref_relative():
    """Relative target_table anchors at the host file's directory."""
    assert (
        vault_datatable.resolve_ref("data/tasks/tasks.md", "../projects/projects.md")
        == "data/projects/projects.md"
    )
    assert (
        vault_datatable.resolve_ref("data/tasks.md", "./tags.md")
        == "data/tags.md"
    )


def test_resolve_ref_absolute_passthrough():
    """Non-relative target_table is treated as vault-absolute."""
    assert (
        vault_datatable.resolve_ref("data/tasks.md", "people/people.md")
        == "people/people.md"
    )


def test_resolve_ref_empty():
    assert vault_datatable.resolve_ref("anywhere.md", "") == ""


def test_is_junction_auto_detect():
    """Two ref fields and no other content fields → junction."""
    schema = {
        "fields": [
            {"name": "order_id", "kind": "ref", "target_table": "orders.md"},
            {"name": "product_id", "kind": "ref", "target_table": "products.md"},
        ],
    }
    assert vault_datatable.is_junction(schema) is True


def test_is_junction_extra_payload_not_auto():
    """A junction-shaped table with extra payload columns is NOT auto-detected."""
    schema = {
        "fields": [
            {"name": "order_id", "kind": "ref", "target_table": "orders.md"},
            {"name": "product_id", "kind": "ref", "target_table": "products.md"},
            {"name": "qty", "kind": "number"},
        ],
    }
    assert vault_datatable.is_junction(schema) is False


def test_is_junction_explicit_override_true():
    """Explicit table.is_junction: true wins even with payload columns."""
    schema = {
        "table": {"is_junction": True},
        "fields": [
            {"name": "a", "kind": "ref", "target_table": "x.md"},
            {"name": "b", "kind": "ref", "target_table": "y.md"},
            {"name": "qty", "kind": "number"},
        ],
    }
    assert vault_datatable.is_junction(schema) is True


def test_is_junction_explicit_override_false():
    """Explicit is_junction: false suppresses auto-detection."""
    schema = {
        "table": {"is_junction": False},
        "fields": [
            {"name": "a", "kind": "ref", "target_table": "x.md"},
            {"name": "b", "kind": "ref", "target_table": "y.md"},
        ],
    }
    assert vault_datatable.is_junction(schema) is False


def test_is_junction_single_ref():
    """A table with only one ref field is not a junction."""
    schema = {
        "fields": [
            {"name": "a", "kind": "ref", "target_table": "x.md"},
            {"name": "name", "kind": "text"},
        ],
    }
    assert vault_datatable.is_junction(schema) is False


def test_validate_schema_ref_missing_target():
    schema = {"fields": [{"name": "p", "kind": "ref"}]}
    warnings = vault_datatable.validate_schema(schema)
    assert any("target_table" in w for w in warnings)


def test_validate_schema_ref_invalid_cardinality():
    schema = {
        "fields": [
            {"name": "p", "kind": "ref", "target_table": "x.md", "cardinality": "wat"},
        ],
    }
    warnings = vault_datatable.validate_schema(schema)
    assert any("cardinality" in w for w in warnings)


def test_validate_schema_ref_clean():
    schema = {
        "fields": [
            {"name": "p", "kind": "ref", "target_table": "x.md", "cardinality": "one"},
        ],
    }
    assert vault_datatable.validate_schema(schema) == []


# ── vault_datatable_index: databases discovery ────────────────────────────────


def test_list_databases_groups_by_folder():
    """Folders containing ≥1 data-table file appear as databases."""
    from nexus import vault_datatable_index

    vault_datatable.create_table(
        "shop/customers.md", {"title": "Customers", "fields": [{"name": "id", "kind": "text"}]},
    )
    vault_datatable.create_table(
        "shop/orders.md", {"title": "Orders", "fields": [{"name": "id", "kind": "text"}]},
    )
    vault_datatable.create_table(
        "people/contacts.md", {"title": "Contacts", "fields": [{"name": "id", "kind": "text"}]},
    )

    dbs = vault_datatable_index.list_databases()
    by_folder = {db["folder"]: db for db in dbs}
    assert "shop" in by_folder
    assert by_folder["shop"]["table_count"] == 2
    assert by_folder["shop"]["title"] == "shop"
    assert "people" in by_folder
    assert by_folder["people"]["table_count"] == 1


def test_list_tables_in_folder():
    from nexus import vault_datatable_index

    vault_datatable.create_table(
        "shop/customers.md", {"title": "Customers", "fields": [{"name": "id", "kind": "text"}]},
    )
    vault_datatable.add_row("shop/customers.md", {"id": "C-1"})
    vault_datatable.add_row("shop/customers.md", {"id": "C-2"})
    vault_datatable.create_table(
        "shop/orders.md", {"title": "Orders", "fields": [{"name": "id", "kind": "text"}]},
    )

    tables = vault_datatable_index.list_tables_in_folder("shop")
    assert len(tables) == 2
    by_path = {t["path"]: t for t in tables}
    assert by_path["shop/customers.md"]["row_count"] == 2
    assert by_path["shop/customers.md"]["title"] == "Customers"
    assert by_path["shop/orders.md"]["row_count"] == 0


def test_list_databases_excludes_non_table_md():
    """Plain markdown files don't make a folder into a database."""
    from nexus import vault_datatable_index
    from nexus import vault as vault_mod

    vault_mod.write_file("notes/diary.md", "# Just a note\n")
    vault_datatable.create_table(
        "notes/contacts.md", {"fields": [{"name": "id", "kind": "text"}]},
    )

    dbs = vault_datatable_index.list_databases()
    notes = [d for d in dbs if d["folder"] == "notes"]
    assert len(notes) == 1
    assert notes[0]["table_count"] == 1


# ── Inbound refs / related rows ───────────────────────────────────────────────


def test_find_inbound_refs_one_to_many():
    """A ref from orders → customers shows up as inbound on customers."""
    vault_datatable.create_table(
        "shop/customers.md",
        {"title": "Customers", "fields": [{"name": "id", "kind": "text"}]},
    )
    vault_datatable.create_table(
        "shop/orders.md",
        {
            "title": "Orders",
            "fields": [
                {"name": "id", "kind": "text"},
                {
                    "name": "customer_id",
                    "kind": "ref",
                    "target_table": "./customers.md",
                    "cardinality": "one",
                },
            ],
        },
    )
    refs = vault_datatable.find_inbound_refs("shop/customers.md")
    assert len(refs) == 1
    assert refs[0]["from_table"] == "shop/orders.md"
    assert refs[0]["field_name"] == "customer_id"
    assert refs[0]["is_junction"] is False


def test_find_inbound_refs_resolves_relative_targets():
    """Relative ../folder/file.md targets resolve to the same path as absolute."""
    vault_datatable.create_table(
        "data/projects/projects.md",
        {"fields": [{"name": "id", "kind": "text"}]},
    )
    vault_datatable.create_table(
        "data/tasks/tasks.md",
        {
            "fields": [
                {"name": "id", "kind": "text"},
                {
                    "name": "project",
                    "kind": "ref",
                    "target_table": "../projects/projects.md",
                    "cardinality": "one",
                },
            ],
        },
    )
    refs = vault_datatable.find_inbound_refs("data/projects/projects.md")
    assert len(refs) == 1
    assert refs[0]["from_table"] == "data/tasks/tasks.md"


def test_related_rows_one_to_many():
    """Customer C-1 sees its orders as inbound 1:N rows."""
    vault_datatable.create_table(
        "shop/customers.md",
        {"fields": [{"name": "id", "kind": "text"}]},
    )
    vault_datatable.add_row("shop/customers.md", {"id": "C-1"})
    vault_datatable.add_row("shop/customers.md", {"id": "C-2"})
    vault_datatable.create_table(
        "shop/orders.md",
        {
            "fields": [
                {"name": "id", "kind": "text"},
                {
                    "name": "customer_id",
                    "kind": "ref",
                    "target_table": "./customers.md",
                    "cardinality": "one",
                },
            ],
        },
    )
    vault_datatable.add_row("shop/orders.md", {"id": "O-1", "customer_id": "C-1"})
    vault_datatable.add_row("shop/orders.md", {"id": "O-2", "customer_id": "C-1"})
    vault_datatable.add_row("shop/orders.md", {"id": "O-3", "customer_id": "C-2"})

    rel = vault_datatable.related_rows("shop/customers.md", "C-1")
    assert len(rel["one_to_many"]) == 1
    assert rel["one_to_many"][0]["count"] == 2
    assert rel["many_to_many"] == []
    assert {r["id"] for r in rel["one_to_many"][0]["rows"]} == {"O-1", "O-2"}


def test_related_rows_many_to_many_via_junction():
    """N:N: orders ↔ items via order_items junction surfaces items on order, not junction rows."""
    vault_datatable.create_table(
        "shop/orders.md",
        {"fields": [{"name": "id", "kind": "text"}], "table": {"primary_key": "id"}},
    )
    vault_datatable.add_row("shop/orders.md", {"id": "O-1"})

    vault_datatable.create_table(
        "shop/items.md",
        {"fields": [{"name": "sku", "kind": "text"}, {"name": "name", "kind": "text"}],
         "table": {"primary_key": "sku"}},
    )
    vault_datatable.add_row("shop/items.md", {"sku": "WIDGET-1", "name": "Widget"})
    vault_datatable.add_row("shop/items.md", {"sku": "GIZMO-9", "name": "Gizmo"})
    vault_datatable.add_row("shop/items.md", {"sku": "OTHER", "name": "Other"})

    vault_datatable.create_table(
        "shop/order_items.md",
        {
            "fields": [
                {"name": "order_id", "kind": "ref", "target_table": "./orders.md"},
                {"name": "sku", "kind": "ref", "target_table": "./items.md"},
            ],
        },
    )
    vault_datatable.add_row(
        "shop/order_items.md", {"order_id": "O-1", "sku": "WIDGET-1"},
    )
    vault_datatable.add_row(
        "shop/order_items.md", {"order_id": "O-1", "sku": "GIZMO-9"},
    )

    rel = vault_datatable.related_rows("shop/orders.md", "O-1")
    assert rel["one_to_many"] == []
    assert len(rel["many_to_many"]) == 1
    m2m = rel["many_to_many"][0]
    assert m2m["target_table"] == "shop/items.md"
    assert m2m["count"] == 2
    skus = {r["sku"] for r in m2m["rows"]}
    assert skus == {"WIDGET-1", "GIZMO-9"}


def test_er_diagram_one_to_many():
    """Customers ↔ orders renders with the }o--|| cardinality marker."""
    from nexus import vault_datatable_index
    vault_datatable.create_table(
        "shop/customers.md", {"fields": [{"name": "id", "kind": "text"}]},
    )
    vault_datatable.create_table(
        "shop/orders.md",
        {
            "fields": [
                {"name": "id", "kind": "text"},
                {
                    "name": "customer_id",
                    "kind": "ref",
                    "target_table": "./customers.md",
                    "cardinality": "one",
                },
            ],
        },
    )
    erd = vault_datatable_index.er_diagram("shop")
    assert erd.startswith("erDiagram")
    assert "customers {" in erd
    assert "orders {" in erd
    assert "}o--||" in erd
    assert '"customer_id"' in erd


def test_er_diagram_junction_collapses():
    """Two-ref junction without payload renders as a single }o--o{ line."""
    from nexus import vault_datatable_index
    vault_datatable.create_table(
        "shop/orders.md", {"fields": [{"name": "id", "kind": "text"}]},
    )
    vault_datatable.create_table(
        "shop/items.md", {"fields": [{"name": "sku", "kind": "text"}]},
    )
    vault_datatable.create_table(
        "shop/order_items.md",
        {
            "fields": [
                {"name": "order_id", "kind": "ref", "target_table": "./orders.md"},
                {"name": "sku", "kind": "ref", "target_table": "./items.md"},
            ],
        },
    )
    erd = vault_datatable_index.er_diagram("shop")
    assert "}o--o{" in erd
    # The junction edge should not generate a separate node-level }o--|| line.
    assert erd.count("}o--||") == 0


def test_er_diagram_empty_folder():
    from nexus import vault_datatable_index
    assert vault_datatable_index.er_diagram("nope") == "erDiagram"


def test_related_rows_trims_whitespace_in_stored_value():
    """A stored ref with trailing/leading whitespace still matches its row."""
    vault_datatable.create_table(
        "shop/customers.md",
        {"fields": [{"name": "id", "kind": "text"}], "table": {"primary_key": "id"}},
    )
    vault_datatable.add_row("shop/customers.md", {"id": "C-1"})
    vault_datatable.create_table(
        "shop/orders.md",
        {
            "fields": [
                {"name": "id", "kind": "text"},
                {
                    "name": "customer_id",
                    "kind": "ref",
                    "target_table": "./customers.md",
                    "cardinality": "one",
                },
            ],
        },
    )
    # Stored value has trailing whitespace — typical of CSV-imported data.
    vault_datatable.add_row(
        "shop/orders.md", {"id": "O-1", "customer_id": " C-1 "},
    )

    rel = vault_datatable.related_rows("shop/customers.md", "C-1")
    assert rel["one_to_many"][0]["count"] == 1
    assert rel["one_to_many"][0]["rows"][0]["id"] == "O-1"


def test_related_rows_falls_back_to_id_when_pk_declared():
    """When the host has explicit primary_key, refs storing the auto _id still match."""
    vault_datatable.create_table(
        "shop/customers.md",
        {"fields": [{"name": "id", "kind": "text"}], "table": {"primary_key": "id"}},
    )
    customer = vault_datatable.add_row("shop/customers.md", {"id": "C-1"})

    vault_datatable.create_table(
        "shop/orders.md",
        {
            "fields": [
                {"name": "id", "kind": "text"},
                {
                    "name": "customer_id",
                    "kind": "ref",
                    "target_table": "./customers.md",
                    "cardinality": "one",
                },
            ],
        },
    )
    # Order references the auto _id (legacy/imported), not the user-facing pk.
    vault_datatable.add_row(
        "shop/orders.md", {"id": "O-1", "customer_id": customer["_id"]},
    )

    rel = vault_datatable.related_rows("shop/customers.md", "C-1")
    assert rel["one_to_many"][0]["count"] == 1
    assert rel["one_to_many"][0]["rows"][0]["id"] == "O-1"


def test_related_rows_no_id_fallback_without_explicit_pk():
    """Without table.primary_key, an _id-storing ref does NOT match the natural key."""
    vault_datatable.create_table(
        "shop/customers.md", {"fields": [{"name": "id", "kind": "text"}]},
    )
    customer = vault_datatable.add_row("shop/customers.md", {"id": "C-1"})

    vault_datatable.create_table(
        "shop/orders.md",
        {
            "fields": [
                {"name": "id", "kind": "text"},
                {
                    "name": "customer_id",
                    "kind": "ref",
                    "target_table": "./customers.md",
                    "cardinality": "one",
                },
            ],
        },
    )
    vault_datatable.add_row(
        "shop/orders.md", {"id": "O-1", "customer_id": customer["_id"]},
    )

    rel = vault_datatable.related_rows("shop/customers.md", "C-1")
    # No fallback when PK isn't explicitly declared — keeps strict semantics
    # for tables that genuinely use the natural key everywhere.
    assert rel["one_to_many"][0]["count"] == 0


def test_related_rows_unmatched_sample_when_empty():
    """Empty match list surfaces a small sample of stored values for debugging."""
    vault_datatable.create_table(
        "shop/customers.md", {"fields": [{"name": "id", "kind": "text"}]},
    )
    vault_datatable.add_row("shop/customers.md", {"id": "C-1"})

    vault_datatable.create_table(
        "shop/orders.md",
        {
            "fields": [
                {"name": "id", "kind": "text"},
                {
                    "name": "customer_id",
                    "kind": "ref",
                    "target_table": "./customers.md",
                    "cardinality": "one",
                },
            ],
        },
    )
    vault_datatable.add_row("shop/orders.md", {"id": "O-1", "customer_id": "C-2"})
    vault_datatable.add_row("shop/orders.md", {"id": "O-2", "customer_id": "C-3"})

    rel = vault_datatable.related_rows("shop/customers.md", "C-1")
    entry = rel["one_to_many"][0]
    assert entry["count"] == 0
    assert set(entry["unmatched_sample"]) == {"C-2", "C-3"}


def test_related_rows_no_sample_when_match_succeeds():
    """unmatched_sample stays empty when the join produced rows."""
    vault_datatable.create_table(
        "shop/customers.md", {"fields": [{"name": "id", "kind": "text"}]},
    )
    vault_datatable.add_row("shop/customers.md", {"id": "C-1"})
    vault_datatable.create_table(
        "shop/orders.md",
        {
            "fields": [
                {"name": "id", "kind": "text"},
                {
                    "name": "customer_id",
                    "kind": "ref",
                    "target_table": "./customers.md",
                    "cardinality": "one",
                },
            ],
        },
    )
    vault_datatable.add_row("shop/orders.md", {"id": "O-1", "customer_id": "C-1"})
    vault_datatable.add_row("shop/orders.md", {"id": "O-2", "customer_id": "C-9"})

    rel = vault_datatable.related_rows("shop/customers.md", "C-1")
    assert rel["one_to_many"][0]["count"] == 1
    assert rel["one_to_many"][0]["unmatched_sample"] == []


def test_related_rows_case_sensitive():
    """Case differences in IDs are preserved — c-1 does not match C-1."""
    vault_datatable.create_table(
        "shop/customers.md", {"fields": [{"name": "id", "kind": "text"}]},
    )
    vault_datatable.add_row("shop/customers.md", {"id": "C-1"})
    vault_datatable.create_table(
        "shop/orders.md",
        {
            "fields": [
                {"name": "id", "kind": "text"},
                {
                    "name": "customer_id",
                    "kind": "ref",
                    "target_table": "./customers.md",
                    "cardinality": "one",
                },
            ],
        },
    )
    vault_datatable.add_row("shop/orders.md", {"id": "O-1", "customer_id": "c-1"})

    rel = vault_datatable.related_rows("shop/customers.md", "C-1")
    assert rel["one_to_many"][0]["count"] == 0


def test_related_rows_cardinality_many_array_match():
    """A field with cardinality=many storing a list still matches by membership."""
    vault_datatable.create_table(
        "tags.md", {"fields": [{"name": "name", "kind": "text"}]},
    )
    vault_datatable.add_row("tags.md", {"name": "urgent"})
    vault_datatable.create_table(
        "tasks.md",
        {
            "fields": [
                {"name": "id", "kind": "text"},
                {
                    "name": "tags",
                    "kind": "ref",
                    "target_table": "tags.md",
                    "cardinality": "many",
                },
                {"name": "label", "kind": "text"},
            ],
        },
    )
    vault_datatable.add_row(
        "tasks.md", {"id": "T-1", "tags": ["urgent", "bug"], "label": "do it"},
    )
    vault_datatable.add_row(
        "tasks.md", {"id": "T-2", "tags": ["nice-to-have"], "label": "later"},
    )

    rel = vault_datatable.related_rows("tags.md", "urgent")
    # tasks.md has a non-ref column ("label"), so it's NOT a junction → 1:N.
    assert len(rel["one_to_many"]) == 1
    assert rel["one_to_many"][0]["count"] == 1
    assert rel["one_to_many"][0]["rows"][0]["id"] == "T-1"


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
    assert result["total"] == 1
    assert result["offset"] == 0
    assert result["limit"] == 100
    assert result["truncated"] is False
    assert result["rows"][0]["x"] == "hello"


def test_tool_list_rows_paginates():
    handle_datatable_tool({
        "action": "create_table",
        "path": "page.md",
        "schema": {"fields": [{"name": "n", "kind": "number"}]},
    })
    handle_datatable_tool({
        "action": "add_rows",
        "path": "page.md",
        "rows": [{"n": i} for i in range(250)],
    })
    # Default page (limit=100, offset=0).
    p1 = json.loads(handle_datatable_tool({"action": "list_rows", "path": "page.md"}))
    assert p1["count"] == 100
    assert p1["total"] == 250
    assert p1["offset"] == 0
    assert p1["truncated"] is True
    assert p1["rows"][0]["n"] == 0
    assert p1["rows"][-1]["n"] == 99
    # Second page.
    p2 = json.loads(handle_datatable_tool({
        "action": "list_rows",
        "path": "page.md",
        "offset": 100,
        "limit": 100,
    }))
    assert p2["count"] == 100
    assert p2["offset"] == 100
    assert p2["truncated"] is True
    assert p2["rows"][0]["n"] == 100
    # Tail page.
    p3 = json.loads(handle_datatable_tool({
        "action": "list_rows",
        "path": "page.md",
        "offset": 200,
        "limit": 100,
    }))
    assert p3["count"] == 50
    assert p3["truncated"] is False


def test_tool_list_rows_caps_limit_at_1000():
    handle_datatable_tool({
        "action": "create_table",
        "path": "cap.md",
        "schema": {"fields": [{"name": "n", "kind": "number"}]},
    })
    handle_datatable_tool({
        "action": "add_rows",
        "path": "cap.md",
        "rows": [{"n": i} for i in range(5)],
    })
    out = json.loads(handle_datatable_tool({
        "action": "list_rows",
        "path": "cap.md",
        "limit": 999_999,
    }))
    assert out["limit"] == 1000  # capped, not the requested value


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


def test_tool_add_field():
    handle_datatable_tool({
        "action": "create_table",
        "path": "f.md",
        "schema": {"fields": [{"name": "id", "kind": "text"}]},
    })
    result = json.loads(handle_datatable_tool({
        "action": "add_field",
        "path": "f.md",
        "field": {"name": "qty", "kind": "number"},
    }))
    assert result["ok"] is True
    assert any(f["name"] == "qty" for f in result["table"]["schema"]["fields"])


def test_tool_rename_field_migrates_rows():
    handle_datatable_tool({
        "action": "create_table",
        "path": "rn.md",
        "schema": {"fields": [{"name": "old_name", "kind": "text"}]},
    })
    handle_datatable_tool({
        "action": "add_row",
        "path": "rn.md",
        "row": {"old_name": "hi"},
    })
    result = json.loads(handle_datatable_tool({
        "action": "rename_field",
        "path": "rn.md",
        "field_name": "old_name",
        "new_name": "new_name",
    }))
    assert result["ok"] is True
    assert result["table"]["rows"][0].get("new_name") == "hi"
    assert "old_name" not in result["table"]["rows"][0]


def test_tool_create_relation():
    handle_datatable_tool({
        "action": "create_table",
        "path": "shop/customers.md",
        "schema": {"fields": [{"name": "id", "kind": "text"}]},
    })
    handle_datatable_tool({
        "action": "create_table",
        "path": "shop/orders.md",
        "schema": {"fields": [{"name": "id", "kind": "text"}]},
    })
    result = json.loads(handle_datatable_tool({
        "action": "create_relation",
        "path": "shop/orders.md",
        "field_name": "customer_id",
        "target_table": "./customers.md",
        "cardinality": "one",
    }))
    assert result["ok"] is True
    fields = result["table"]["schema"]["fields"]
    customer_field = next(f for f in fields if f["name"] == "customer_id")
    assert customer_field["kind"] == "ref"
    assert customer_field["target_table"] == "./customers.md"


def test_tool_create_junction():
    handle_datatable_tool({
        "action": "create_table",
        "path": "shop/orders.md",
        "schema": {"fields": [{"name": "id", "kind": "text"}]},
    })
    handle_datatable_tool({
        "action": "create_table",
        "path": "shop/items.md",
        "schema": {"fields": [{"name": "sku", "kind": "text"}]},
    })
    result = json.loads(handle_datatable_tool({
        "action": "create_junction",
        "path": "shop/order_items.md",
        "table_a": "./orders.md",
        "table_b": "./items.md",
    }))
    assert result["ok"] is True
    fields = result["table"]["schema"]["fields"]
    assert len(fields) == 2
    assert all(f["kind"] == "ref" for f in fields)


def test_tool_er_diagram():
    handle_datatable_tool({
        "action": "create_table",
        "path": "db/a.md",
        "schema": {"fields": [{"name": "id", "kind": "text"}]},
    })
    handle_datatable_tool({
        "action": "create_table",
        "path": "db/b.md",
        "schema": {
            "fields": [
                {"name": "id", "kind": "text"},
                {
                    "name": "a_id",
                    "kind": "ref",
                    "target_table": "./a.md",
                    "cardinality": "one",
                },
            ],
        },
    })
    result = json.loads(handle_datatable_tool({
        "action": "er_diagram",
        "folder": "db",
    }))
    assert result["ok"] is True
    assert result["mermaid"].startswith("erDiagram")
    assert "}o--||" in result["mermaid"]


def test_tool_list_databases():
    handle_datatable_tool({
        "action": "create_table",
        "path": "shop/customers.md",
        "schema": {"fields": [{"name": "id", "kind": "text"}]},
    })
    result = json.loads(handle_datatable_tool({"action": "list_databases"}))
    assert result["ok"] is True
    assert any(db["folder"] == "shop" for db in result["databases"])


def test_tool_related_rows():
    handle_datatable_tool({
        "action": "create_table",
        "path": "shop/customers.md",
        "schema": {"fields": [{"name": "id", "kind": "text"}], "table": {"primary_key": "id"}},
    })
    handle_datatable_tool({
        "action": "add_row",
        "path": "shop/customers.md",
        "row": {"id": "C-1"},
    })
    handle_datatable_tool({
        "action": "create_table",
        "path": "shop/orders.md",
        "schema": {
            "fields": [
                {"name": "id", "kind": "text"},
                {
                    "name": "customer_id",
                    "kind": "ref",
                    "target_table": "./customers.md",
                    "cardinality": "one",
                },
            ],
        },
    })
    handle_datatable_tool({
        "action": "add_row",
        "path": "shop/orders.md",
        "row": {"id": "O-1", "customer_id": "C-1"},
    })
    result = json.loads(handle_datatable_tool({
        "action": "related_rows",
        "path": "shop/customers.md",
        "row_id": "C-1",
    }))
    assert result["ok"] is True
    assert len(result["one_to_many"]) == 1
    assert result["one_to_many"][0]["count"] == 1


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
