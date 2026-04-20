"""Tests for MemoryHandler — read/write/validation."""

from __future__ import annotations

import pytest

from nexus.tools.memory_tool import MemoryHandler, _validate_key


# ── key validation ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", [
    "simple",
    "with-hyphens",
    "with/slash",
    "nested/deep/key",
    "abc123",
    "a-b/c_d",
])
def test_valid_keys(key: str) -> None:
    assert _validate_key(key) is None


@pytest.mark.parametrize("key,fragment", [
    ("", "empty"),
    ("UPPER", "[a-z0-9/_-]+"),
    ("has space", "[a-z0-9/_-]+"),
    ("../traversal", ".."),
    ("a/../b", ".."),
    ("a" * 129, "too long"),
])
def test_invalid_keys(key: str, fragment: str) -> None:
    err = _validate_key(key)
    assert err is not None
    assert fragment in err


# ── read ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_missing_key(tmp_path, monkeypatch) -> None:
    import nexus.tools.memory_tool as mt
    monkeypatch.setattr(mt, "MEMORY_DIR", tmp_path)
    handler = MemoryHandler()
    result = await handler.read("missing/key")
    assert "not found" in result


@pytest.mark.asyncio
async def test_read_invalid_key(tmp_path, monkeypatch) -> None:
    import nexus.tools.memory_tool as mt
    monkeypatch.setattr(mt, "MEMORY_DIR", tmp_path)
    handler = MemoryHandler()
    result = await handler.read("INVALID_KEY")
    assert result.startswith("error:")


@pytest.mark.asyncio
async def test_read_existing_key(tmp_path, monkeypatch) -> None:
    import nexus.tools.memory_tool as mt
    monkeypatch.setattr(mt, "MEMORY_DIR", tmp_path)
    # Pre-create the file
    (tmp_path / "mykey.md").write_text("hello world", encoding="utf-8")
    handler = MemoryHandler()
    result = await handler.read("mykey")
    assert result == "hello world"


@pytest.mark.asyncio
async def test_read_nested_key(tmp_path, monkeypatch) -> None:
    import nexus.tools.memory_tool as mt
    monkeypatch.setattr(mt, "MEMORY_DIR", tmp_path)
    nested = tmp_path / "projects"
    nested.mkdir()
    (nested / "nexus.md").write_text("project notes", encoding="utf-8")
    handler = MemoryHandler()
    result = await handler.read("projects/nexus")
    assert result == "project notes"


# ── write ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_creates_file(tmp_path, monkeypatch) -> None:
    import nexus.tools.memory_tool as mt
    monkeypatch.setattr(mt, "MEMORY_DIR", tmp_path)
    handler = MemoryHandler()
    result = await handler.write("newkey", "# Note\nsome content")
    assert result == "ok"
    assert (tmp_path / "newkey.md").read_text(encoding="utf-8") == "# Note\nsome content"


@pytest.mark.asyncio
async def test_write_creates_intermediate_dirs(tmp_path, monkeypatch) -> None:
    import nexus.tools.memory_tool as mt
    monkeypatch.setattr(mt, "MEMORY_DIR", tmp_path)
    handler = MemoryHandler()
    result = await handler.write("deep/nested/key", "content")
    assert result == "ok"
    assert (tmp_path / "deep" / "nested" / "key.md").exists()


@pytest.mark.asyncio
async def test_write_overwrites_existing(tmp_path, monkeypatch) -> None:
    import nexus.tools.memory_tool as mt
    monkeypatch.setattr(mt, "MEMORY_DIR", tmp_path)
    (tmp_path / "key.md").write_text("old content", encoding="utf-8")
    handler = MemoryHandler()
    await handler.write("key", "new content")
    assert (tmp_path / "key.md").read_text(encoding="utf-8") == "new content"


@pytest.mark.asyncio
async def test_write_invalid_key(tmp_path, monkeypatch) -> None:
    import nexus.tools.memory_tool as mt
    monkeypatch.setattr(mt, "MEMORY_DIR", tmp_path)
    handler = MemoryHandler()
    result = await handler.write("INVALID", "content")
    assert result.startswith("error:")


@pytest.mark.asyncio
async def test_write_content_too_large(tmp_path, monkeypatch) -> None:
    import nexus.tools.memory_tool as mt
    monkeypatch.setattr(mt, "MEMORY_DIR", tmp_path)
    handler = MemoryHandler()
    huge = "x" * 50_001
    result = await handler.write("key", huge)
    assert "too large" in result


@pytest.mark.asyncio
async def test_write_path_traversal_rejected(tmp_path, monkeypatch) -> None:
    import nexus.tools.memory_tool as mt
    monkeypatch.setattr(mt, "MEMORY_DIR", tmp_path)
    handler = MemoryHandler()
    result = await handler.write("../escape", "bad content")
    assert result.startswith("error:")
    assert not (tmp_path.parent / "escape.md").exists()
