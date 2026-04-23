"""Tests for MemoryHandler — read/write via MemoryStore delegation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nexus.tools.memory_tool import MemoryHandler, _validate_key


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


def _mock_store():
    store = AsyncMock()
    store.read = AsyncMock(return_value=None)
    store.write = AsyncMock(return_value=None)
    return store


@pytest.mark.asyncio
async def test_read_missing_key() -> None:
    store = _mock_store()
    store.read.return_value = None
    handler = MemoryHandler()
    handler._store = store
    result = await handler.read("missing/key")
    assert "not found" in result


@pytest.mark.asyncio
async def test_read_invalid_key() -> None:
    handler = MemoryHandler()
    result = await handler.read("INVALID_KEY")
    assert result.startswith("error:")


@pytest.mark.asyncio
async def test_read_existing_key() -> None:
    entry = MagicMock()
    entry.content = "hello world"
    store = _mock_store()
    store.read.return_value = entry
    handler = MemoryHandler()
    handler._store = store
    result = await handler.read("mykey")
    assert "hello world" in result


@pytest.mark.asyncio
async def test_write_creates_file() -> None:
    store = _mock_store()
    handler = MemoryHandler()
    handler._store = store
    result = await handler.write("newkey", "# Note\nsome content")
    assert result == "ok"
    store.write.assert_awaited_once_with(
        "newkey", "# Note\nsome content", category="notes", tags=[],
    )


@pytest.mark.asyncio
async def test_write_with_tags() -> None:
    store = _mock_store()
    handler = MemoryHandler()
    handler._store = store
    result = await handler.write("tagged", "content", tags=["go", "project"])
    assert result == "ok"
    store.write.assert_awaited_once_with(
        "tagged", "content", category="notes", tags=["go", "project"],
    )


@pytest.mark.asyncio
async def test_write_without_tags() -> None:
    store = _mock_store()
    handler = MemoryHandler()
    handler._store = store
    result = await handler.write("plain", "just content")
    assert result == "ok"
    store.write.assert_awaited_once_with(
        "plain", "just content", category="notes", tags=[],
    )


@pytest.mark.asyncio
async def test_write_invalid_key() -> None:
    handler = MemoryHandler()
    result = await handler.write("INVALID", "content")
    assert result.startswith("error:")


@pytest.mark.asyncio
async def test_write_content_too_large() -> None:
    handler = MemoryHandler()
    huge = "x" * 50_001
    result = await handler.write("key", huge)
    assert "too large" in result


@pytest.mark.asyncio
async def test_write_path_traversal_rejected() -> None:
    handler = MemoryHandler()
    result = await handler.write("../escape", "bad content")
    assert result.startswith("error:")
