"""Tests for memory_recall tool backed by loom.MemoryStore."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect MEMORY_DIR, _LOOM_STORE_DIR, and _store to tmp directories."""
    import nexus.tools.memory_tool as mt

    mem_dir = tmp_path / "memory"
    loom_dir = tmp_path / "memory" / ".loom"
    mem_dir.mkdir()
    loom_dir.mkdir()
    monkeypatch.setattr(mt, "MEMORY_DIR", mem_dir)
    monkeypatch.setattr(mt, "_LOOM_STORE_DIR", loom_dir)
    monkeypatch.setattr(mt, "_MEMORY_INDEX", loom_dir / "_index.sqlite")
    # Reset the singleton so each test gets a fresh store pointed at tmp_path.
    monkeypatch.setattr(mt, "_store", None)
    yield
    # Cleanup: reset singleton after test.
    mt._store = None


async def test_memory_recall_returns_empty_when_no_memories():
    from nexus.tools.memory_tool import MemoryHandler

    result = await MemoryHandler().recall("anything")
    assert result == "(no memories stored yet)"


async def test_memory_write_then_recall_finds_it():
    from nexus.tools.memory_tool import MemoryHandler

    h = MemoryHandler()
    # Use content that contains a distinctive searchable substring so the
    # LIKE-based non-FTS5 fallback (used when SQLite FTS5 is unavailable)
    # can still match. Single-word recall works in both FTS5 and LIKE modes.
    await h.write("project/alpha", "Alpha project uses FastAPI for the backend service.")
    await h.write("project/beta", "Beta project is a mobile app written in Swift.")

    result = await h.recall("FastAPI", limit=5)

    # The alpha entry should appear (it contains the query term).
    assert "project/alpha" in result


async def test_memory_recall_respects_limit():
    from nexus.tools.memory_tool import MemoryHandler

    h = MemoryHandler()
    for i in range(5):
        await h.write(f"note/item-{i}", f"This is note number {i} about various topics.")

    result = await h.recall("note about topics", limit=2)

    # Count how many key headings appear in the result.
    hits = result.split("---")
    assert len(hits) <= 2
