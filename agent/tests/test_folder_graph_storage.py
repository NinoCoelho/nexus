"""Hidden-dir manifest + ontology snapshot round-trip tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus.agent.folder_graph import _storage


def test_normalize_resolves_symlinks(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)

    assert _storage.normalize_folder(link) == real.resolve()


def test_normalize_strips_trailing_slash(tmp_path: Path) -> None:
    p = tmp_path / "foo"
    p.mkdir()
    assert _storage.normalize_folder(f"{p}/") == p.resolve()


def test_is_initialized_false_before_open(tmp_path: Path) -> None:
    assert _storage.is_initialized(tmp_path) is False


def test_open_manifest_creates_hidden_dir_and_tables(tmp_path: Path) -> None:
    conn = _storage.open_manifest(tmp_path)
    try:
        assert (tmp_path / ".nexus-graph" / "manifest.sqlite").is_file()
        assert _storage.is_initialized(tmp_path) is True
        # Schema version stamped
        version = _storage.get_meta_kv(conn, "schema_version")
        assert version == str(_storage.SCHEMA_VERSION)
        # Tables exist (no exception)
        conn.execute("SELECT * FROM files")
        conn.execute("SELECT * FROM meta")
    finally:
        conn.close()


def test_save_and_load_meta_round_trip(tmp_path: Path) -> None:
    ontology = {
        "entity_types": ["person", "project"],
        "relations": ["mentions", "uses"],
        "allow_custom_relations": True,
    }
    _storage.save_meta(tmp_path, ontology=ontology, embedder_id="test-emb",
                       extractor_id="builtin")
    meta = _storage.load_meta(tmp_path)

    assert meta["ontology"] == ontology
    assert meta["ontology_hash"] == _storage.ontology_hash(ontology)
    assert meta["embedder_id"] == "test-emb"
    assert meta["extractor_id"] == "builtin"
    assert meta["file_count"] == 0
    assert meta["last_indexed_at"] is None


def test_load_meta_returns_empty_for_unindexed(tmp_path: Path) -> None:
    assert _storage.load_meta(tmp_path) == {}


def test_ontology_hash_is_order_invariant() -> None:
    a = {"entity_types": ["a", "b"], "relations": ["x", "y"], "allow_custom_relations": True}
    b = {"entity_types": ["b", "a"], "relations": ["y", "x"], "allow_custom_relations": True}
    assert _storage.ontology_hash(a) == _storage.ontology_hash(b)


def test_upsert_and_is_file_current_mtime_match(tmp_path: Path) -> None:
    conn = _storage.open_manifest(tmp_path)
    try:
        _storage.upsert_file(conn, "a.md", mtime=1234.0, size=10, hash_="abc")
        # Same mtime → trusted, hash agrees → current
        assert _storage.is_file_current(conn, "a.md", mtime=1234.0, hash_="abc") is True
        # Same mtime, different hash → mtime trumps the cheap path:
        # the function returns based on saved hash equality with given hash
        assert _storage.is_file_current(conn, "a.md", mtime=1234.0, hash_="zzz") is False
        # Different mtime, same hash → re-checked, still current
        assert _storage.is_file_current(conn, "a.md", mtime=9999.0, hash_="abc") is True
        # Unknown file → False
        assert _storage.is_file_current(conn, "missing.md", mtime=0.0, hash_="x") is False
    finally:
        conn.close()


def test_remove_file_drops_row(tmp_path: Path) -> None:
    conn = _storage.open_manifest(tmp_path)
    try:
        _storage.upsert_file(conn, "a.md", mtime=1.0, size=1, hash_="h")
        _storage.remove_file(conn, "a.md")
        assert _storage.all_indexed_files(conn) == {}
    finally:
        conn.close()
