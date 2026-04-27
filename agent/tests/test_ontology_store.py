"""Tests for the vault-backed OntologyStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus.agent.ontology_store import (
    ENTITY_TYPES_FILE,
    INSTRUCTIONS_FILE,
    META_FILE,
    ONTOLOGY_VAULT_DIR,
    RELATIONS_FILE,
    OntologyStore,
)


@pytest.fixture
def tmp_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate vault writes — point nexus.vault at a temp directory."""
    monkeypatch.setattr("nexus.vault._VAULT_ROOT", tmp_path)
    return tmp_path


def _seed_default(vault: Path) -> OntologyStore:
    store = OntologyStore(vault)
    store.seed_if_empty(
        entity_types=["person", "project", "concept"],
        core_relations=["uses", "depends_on"],
        allow_custom_relations=True,
        type_prototypes={
            "person": ["person individual | pessoa indivíduo"],
            "project": ["project initiative | projeto iniciativa"],
        },
        relation_prototypes={
            "uses": ["uses utilizes | usa utiliza"],
        },
    )
    return store


def test_seed_creates_all_files(tmp_vault: Path) -> None:
    _seed_default(tmp_vault)
    base = tmp_vault / ONTOLOGY_VAULT_DIR
    assert (base / ENTITY_TYPES_FILE).is_file()
    assert (base / RELATIONS_FILE).is_file()
    assert (base / INSTRUCTIONS_FILE).is_file()
    assert (base / META_FILE).is_file()


def test_seed_is_idempotent(tmp_vault: Path) -> None:
    store = _seed_default(tmp_vault)
    # Mutate one file; seed_if_empty should NOT clobber it.
    csv_path = tmp_vault / ONTOLOGY_VAULT_DIR / ENTITY_TYPES_FILE
    csv_path.write_text("type,description,prototypes_en,prototypes_pt\nfoo,,,\n", encoding="utf-8")
    assert store.seed_if_empty(["bar"], ["uses"], True) is False
    assert csv_path.read_text(encoding="utf-8").startswith("type,description")
    assert "foo" in csv_path.read_text(encoding="utf-8")


def test_load_returns_seeded_data(tmp_vault: Path) -> None:
    store = _seed_default(tmp_vault)
    snap = store.load()
    assert snap.type_names() == ["person", "project", "concept"]
    assert snap.relation_names() == ["uses", "depends_on"]
    assert snap.allow_custom_relations is True
    # prototype split round-trips
    person = next(t for t in snap.entity_types if t.type == "person")
    assert person.prototypes_en == "person individual"
    assert person.prototypes_pt == "pessoa indivíduo"


def test_type_prototypes_join_roundtrip(tmp_vault: Path) -> None:
    store = _seed_default(tmp_vault)
    snap = store.load()
    protos = snap.type_prototypes()
    assert protos["person"] == ["person individual | pessoa indivíduo"]
    # Type with no pt side falls back to en alone (no trailing separator).
    assert protos["concept"] == [""] or "|" not in protos["concept"][0]


def test_add_type_appends(tmp_vault: Path) -> None:
    store = _seed_default(tmp_vault)
    store.add_type("meeting", description="syncs", prototypes_en="meeting sync", prototypes_pt="reunião")
    snap = store.load()
    assert "meeting" in snap.type_names()
    meeting = next(t for t in snap.entity_types if t.type == "meeting")
    assert meeting.description == "syncs"
    assert meeting.prototypes_pt == "reunião"


def test_add_type_rejects_duplicate(tmp_vault: Path) -> None:
    store = _seed_default(tmp_vault)
    with pytest.raises(ValueError, match="already exists"):
        store.add_type("person")


def test_add_type_rejects_invalid_name(tmp_vault: Path) -> None:
    store = _seed_default(tmp_vault)
    for bad in ["Meeting", "with-hyphen", "1starts_digit", ""]:
        with pytest.raises(ValueError, match="invalid"):
            store.add_type(bad)


def test_update_type_patches_only_passed_fields(tmp_vault: Path) -> None:
    store = _seed_default(tmp_vault)
    store.update_type("person", prototypes_pt="pessoa novo")
    snap = store.load()
    person = next(t for t in snap.entity_types if t.type == "person")
    assert person.prototypes_pt == "pessoa novo"
    # English side untouched
    assert person.prototypes_en == "person individual"


def test_remove_type(tmp_vault: Path) -> None:
    store = _seed_default(tmp_vault)
    store.remove_type("concept")
    snap = store.load()
    assert "concept" not in snap.type_names()


def test_remove_last_type_rejected(tmp_vault: Path) -> None:
    store = OntologyStore(tmp_vault)
    store.seed_if_empty(["only"], ["uses"], True)
    with pytest.raises(ValueError, match="last entity type"):
        store.remove_type("only")


def test_set_allow_custom_relations(tmp_vault: Path) -> None:
    store = _seed_default(tmp_vault)
    store.set_allow_custom_relations(False)
    snap = store.load()
    assert snap.allow_custom_relations is False


def test_relation_crud(tmp_vault: Path) -> None:
    store = _seed_default(tmp_vault)
    store.add_relation("approved_by", description="auth", prototypes_en="approved by")
    snap = store.load()
    assert "approved_by" in snap.relation_names()

    store.update_relation("approved_by", prototypes_pt="aprovado por")
    snap = store.load()
    rel = next(r for r in snap.relations if r.relation == "approved_by")
    assert rel.prototypes_pt == "aprovado por"

    store.remove_relation("approved_by")
    snap = store.load()
    assert "approved_by" not in snap.relation_names()


def test_csv_roundtrip_handles_embedded_commas_and_quotes(tmp_vault: Path) -> None:
    store = OntologyStore(tmp_vault)
    store.seed_if_empty(["x"], ["y"], True)
    weird = 'has "quotes", commas, and | pipes'
    store.update_type("x", description=weird, prototypes_en=weird)
    snap = store.load()
    x = next(t for t in snap.entity_types if t.type == "x")
    assert x.description == weird
    assert x.prototypes_en == weird
