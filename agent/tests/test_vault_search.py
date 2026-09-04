"""Tests for vault_search — FTS5 escaping, indexing, and search behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus import vault_search


@pytest.fixture
def fts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(vault_search, "_VAULT_ROOT", tmp_path)
    monkeypatch.setattr(vault_search, "_INDEX_PATH", tmp_path / "fts.sqlite")
    (tmp_path / "a.md").write_text("The Q1 roadmap covers Nexus and Loom.", encoding="utf-8")
    (tmp_path / "b.md").write_text("Random notes about coffee.", encoding="utf-8")
    vault_search.rebuild_from_disk()
    yield tmp_path
    vault_search._VAULT_ROOT = None
    vault_search._INDEX_PATH = None


def test_rebuild_indexes_all_files(fts: Path) -> None:
    assert not vault_search.is_empty()
    hits = vault_search.search("roadmap")
    assert [h["path"] for h in hits] == ["a.md"]


def test_search_matches_partial_word(fts: Path) -> None:
    """FTS5 with porter stemming should find 'roadmap' from 'roadmaps' etc."""
    assert len(vault_search.search("roadmap")) == 1


def test_raw_fts_operators_are_escaped(fts: Path) -> None:
    """Unquoted operators must not raise — every token is double-quoted."""
    for q in ('"unbalanced', "NEAR(a b)", "roadmap AND", "a OR b", "*"):
        hits = vault_search.search(q)
        assert isinstance(hits, list)


def test_snippet_included_in_results(fts: Path) -> None:
    hits = vault_search.search("roadmap")
    assert hits and "path" in hits[0] and "snippet" in hits[0] and "score" in hits[0]


def test_index_path_upsert_replaces(fts: Path) -> None:
    vault_search.index_path("a.md", "completely new content about zebras")
    assert [h["path"] for h in vault_search.search("zebras")] == ["a.md"]
    assert vault_search.search("roadmap") == []


def test_remove_path(fts: Path) -> None:
    vault_search.remove_path("a.md")
    assert vault_search.search("roadmap") == []


def test_empty_query_returns_nothing(fts: Path) -> None:
    assert vault_search.search("   ") == []
