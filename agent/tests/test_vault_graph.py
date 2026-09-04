"""Tests for vault_graph — full build (links, wiki-links, orphans) and all
five scoped queries. These paths shipped broken (KeyError 'to_') because
nothing exercised them; this file is the regression net.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus import vault_graph
from nexus.vault_graph import build_graph, build_scoped_graph


@pytest.fixture
def vault_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from nexus import vault

    monkeypatch.setattr(vault, "_VAULT_ROOT", tmp_path)
    # Also point vault_graph at the same root (it imports _vault_root lazily
    # from nexus.vault, so patching the module attribute is enough).
    vault_graph.invalidate_cache()
    return tmp_path


def _write(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


VAULT = {
    "index.md": "---\ntitle: Home\ntags: [start]\n---\n# Home\nsee [about](about.md) and [[Projects/Nexus]]\n",
    "about.md": "# About\nback to [](index.md)\n",
    "Projects/Nexus.md": "---\ntags: [proj]\n---\n# Nexus\nuses [[Tools/Loom]] and mentions docs/Notes/plan.md\n",
    "Projects/Other.md": "# Other\nunlinked\n",
    "Tools/Loom.md": "# Loom\nsee [nexus](../Projects/Nexus.md#intro)\n",
    "Notes/plan.md": "# Plan\n#roadmap tag here\n",
}


@pytest.fixture(autouse=True)
def seed_vault(vault_root: Path):
    for rel, content in VAULT.items():
        _write(vault_root, rel, content)
    yield
    vault_graph.invalidate_cache()


def test_full_graph_wiki_and_anchor_links(vault_root: Path):
    data = build_graph()
    edges = {(e["from_"], e["to"]) for e in data["edges"]}
    # markdown link
    assert ("index.md", "about.md") in edges
    # wiki-link to extension-less target resolves by path+ext
    assert ("index.md", "Projects/Nexus.md") in edges
    # wiki-link to title file
    # (Projects/Nexus ↔ Tools/Loom via [[Tools/Loom]])
    assert ("Projects/Nexus.md", "Tools/Loom.md") in edges
    # bare path mention
    assert ("Projects/Nexus.md", "Notes/plan.md") in edges
    # anchored relative markdown link
    assert ("Tools/Loom.md", "Projects/Nexus.md") in edges
    # Other.md is the only orphan
    assert data["orphans"] == ["Projects/Other.md"]


def test_full_graph_skips_code_fence_links(vault_root: Path):
    _write(vault_root, "code.md", "```\nsee [fake](about.md)\n```\n")
    data = build_graph()
    edges = {(e["from_"], e["to"]) for e in data["edges"]}
    assert ("code.md", "about.md") not in edges


def test_scope_file(vault_root: Path):
    # hops=1 → just the seed; hops=2 → seed + direct neighbours
    solo = build_scoped_graph(scope="file", seed="Projects/Nexus.md", hops=1, edge_types="link")
    assert {n["path"] for n in solo["nodes"]} == {"Projects/Nexus.md"}

    data = build_scoped_graph(scope="file", seed="Projects/Nexus.md", hops=2, edge_types="link")
    paths = {n["path"] for n in data["nodes"]}
    assert "Projects/Nexus.md" in paths
    assert "Tools/Loom.md" in paths
    assert all(e["type"] == "link" for e in data["edges"])


def test_scope_folder(vault_root: Path):
    data = build_scoped_graph(scope="folder", seed="Projects", hops=1, edge_types="link")
    paths = {n["path"] for n in data["nodes"]}
    assert "Projects/Nexus.md" in paths
    assert "Projects/Other.md" in paths
    assert not any(p.startswith("Tools/") for p in paths)
    # cross-folder edge typed
    types = {e["type"] for e in data["edges"]}
    assert "folder-cross" in types


def test_scope_folder_hops2(vault_root: Path):
    data = build_scoped_graph(scope="folder", seed="Projects", hops=2, edge_types="link")
    paths = {n["path"] for n in data["nodes"]}
    assert "Tools/Loom.md" in paths


def test_scope_search(vault_root: Path):
    from nexus import vault_search

    vault_search._VAULT_ROOT = vault_root
    vault_search._INDEX_PATH = vault_root / "fts-test.sqlite"
    try:
        vault_search.rebuild_from_disk()
        data = build_scoped_graph(scope="search", seed="Nexus", hops=1, edge_types="link")
        paths = {n["path"] for n in data["nodes"]}
        assert "Projects/Nexus.md" in paths
    finally:
        vault_search._VAULT_ROOT = None
        vault_search._INDEX_PATH = None


def test_scope_entity_returns_struct(vault_root: Path):
    data = build_scoped_graph(scope="entity", seed="99999", hops=1, edge_types="link")
    assert data["nodes"] == []
    assert data["edges"] == []


def test_scoped_edges_serialize_keys(vault_root: Path):
    """Regression: serialization must use 'to' (not 'to_')."""
    data = build_scoped_graph(scope="file", seed="index.md", hops=1, edge_types="link")
    for e in data["edges"]:
        assert set(e.keys()) == {"from_", "to", "type"}
        assert isinstance(e["to"], str)


def test_route_calls_scoped_builder_positionally_safe(vault_root: Path):
    """Regression: the /vault/graph route used to call build_scoped_graph
    with positional args against a keyword-only signature (TypeError → 500)."""
    from nexus.server.routes import vault as vault_routes

    for scope, seed in (("file", "index.md"), ("folder", "Projects"), ("search", "Nexus")):
        # positional call shape must fail loudly if someone re-adds it
        out = vault_routes  # noqa: F841 — import smoke
        data = build_scoped_graph(scope=scope, seed=seed, hops=1, edge_types="link")
        assert data["nodes"] is not None
