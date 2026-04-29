"""Folder-indexer SSE shape + manifest update tests with a stub engine."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nexus.agent.folder_graph import _indexer, _storage


class _FakeGraph:
    def __init__(self) -> None:
        self.entities = 0
        self.triples = 0

    def count_entities(self) -> int:
        return self.entities

    def count_triples(self) -> int:
        return self.triples


class _FakeEngine:
    def __init__(self) -> None:
        self._entity_graph = _FakeGraph()
        self.indexed: list[tuple[str, str]] = []
        self.removed: list[str] = []

    async def index_source(self, path: str, content: str) -> None:
        self.indexed.append((path, content))
        self._entity_graph.entities += 1
        self._entity_graph.triples += 1

    def remove_source(self, path: str) -> None:
        self.removed.append(path)


@pytest.fixture
def fake_engine_pool(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Patch open_folder_engine to return a stub instead of a real loom engine."""
    engine = _FakeEngine()
    manifest = _storage.open_manifest(tmp_path)
    entry = {
        "engine": engine, "manifest": manifest,
        "embedder_id": "stub", "extractor_id": "stub",
    }

    def _fake_open(folder, ontology, cfg):
        return entry

    monkeypatch.setattr(
        "nexus.agent.folder_graph._indexer.open_folder_engine", _fake_open,
        raising=False,
    )
    # Engine pool is imported lazily inside the function — patch the import too.
    import nexus.agent.folder_graph._engine_pool as ep
    monkeypatch.setattr(ep, "open_folder_engine", _fake_open)

    yield engine, manifest
    manifest.close()


def _parse_sse(stream: list[str]) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for frame in stream:
        lines = frame.strip().split("\n")
        event = lines[0].split(": ", 1)[1]
        data = json.loads(lines[1].split(": ", 1)[1])
        out.append((event, data))
    return out


@pytest.mark.asyncio
async def test_index_streaming_marks_files_and_emits_events(
    tmp_path: Path, fake_engine_pool
) -> None:
    engine, manifest = fake_engine_pool
    (tmp_path / "a.md").write_text("alpha content", encoding="utf-8")
    (tmp_path / "b.md").write_text("beta content", encoding="utf-8")

    cfg = SimpleNamespace(
        graphrag=SimpleNamespace(
            enabled=True,
            embeddings=SimpleNamespace(model="", provider="builtin", base_url="",
                                       key_env="", dimensions=384),
            extraction=SimpleNamespace(model=None, max_gleanings=1),
            embedding_model_id="",
            extraction_model_id="",
            max_hops=2, context_budget=3000, top_k=10, chunk_size=1000,
        ),
    )
    ontology = {"entity_types": ["x"], "relations": ["y"], "allow_custom_relations": True}

    frames = []
    async for f in _indexer.index_folder_streaming(tmp_path, cfg=cfg,
                                                   ontology=ontology, full=True):
        frames.append(f)

    events = _parse_sse(frames)
    event_types = [e for e, _ in events]
    assert "phase" in event_types
    assert "file" in event_types
    assert "stats" in event_types
    assert event_types[-1] == "done"

    # Both files indexed via the fake engine
    indexed_paths = sorted(p for p, _ in engine.indexed)
    assert indexed_paths == ["a.md", "b.md"]

    # Manifest now has both files
    rows = _storage.all_indexed_files(manifest)
    assert set(rows.keys()) == {"a.md", "b.md"}


@pytest.mark.asyncio
async def test_incremental_skips_unchanged_files(
    tmp_path: Path, fake_engine_pool
) -> None:
    engine, manifest = fake_engine_pool
    f = tmp_path / "a.md"
    f.write_text("hello", encoding="utf-8")
    s = f.stat()
    _storage.upsert_file(manifest, "a.md", mtime=s.st_mtime, size=s.st_size,
                         hash_=_storage.content_hash("hello"))

    cfg = SimpleNamespace(graphrag=SimpleNamespace(
        enabled=True,
        embeddings=SimpleNamespace(model="", provider="builtin", base_url="",
                                   key_env="", dimensions=384),
        extraction=SimpleNamespace(model=None, max_gleanings=1),
        embedding_model_id="", extraction_model_id="",
        max_hops=2, context_budget=3000, top_k=10, chunk_size=1000,
    ))
    ontology = {"entity_types": ["x"], "relations": ["y"], "allow_custom_relations": True}

    frames = []
    async for fr in _indexer.index_folder_streaming(tmp_path, cfg=cfg,
                                                    ontology=ontology, full=False):
        frames.append(fr)

    events = _parse_sse(frames)
    file_events = [d for e, d in events if e == "file"]
    # The single existing file should appear, marked skipped.
    assert any(d.get("skipped") for d in file_events)
    # And the engine never had to index it
    assert engine.indexed == []


@pytest.mark.asyncio
async def test_removed_files_get_removed_from_engine(
    tmp_path: Path, fake_engine_pool
) -> None:
    engine, manifest = fake_engine_pool
    # Pre-populate manifest with a file that no longer exists on disk.
    _storage.upsert_file(manifest, "ghost.md", mtime=0, size=0, hash_="abc")

    # And one real file
    (tmp_path / "real.md").write_text("hi", encoding="utf-8")

    cfg = SimpleNamespace(graphrag=SimpleNamespace(
        enabled=True,
        embeddings=SimpleNamespace(model="", provider="builtin", base_url="",
                                   key_env="", dimensions=384),
        extraction=SimpleNamespace(model=None, max_gleanings=1),
        embedding_model_id="", extraction_model_id="",
        max_hops=2, context_budget=3000, top_k=10, chunk_size=1000,
    ))
    ontology = {"entity_types": ["x"], "relations": ["y"], "allow_custom_relations": True}

    frames = []
    async for fr in _indexer.index_folder_streaming(tmp_path, cfg=cfg,
                                                    ontology=ontology, full=False):
        frames.append(fr)

    assert "ghost.md" in engine.removed
    rows = _storage.all_indexed_files(manifest)
    assert "ghost.md" not in rows
