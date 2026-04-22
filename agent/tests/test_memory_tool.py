"""Tests for MemoryHandler — read/write via vault delegation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import nexus.vault
from nexus.tools.memory_tool import MemoryHandler, _validate_key


# ── key validation ─────────────────────────────────────────────────────


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


# ── read ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_missing_key() -> None:
    mock_vault = MagicMock()
    mock_vault.read_file.side_effect = FileNotFoundError("nope")
    with patch.object(nexus.vault, "read_file", mock_vault.read_file):
        handler = MemoryHandler()
        result = await handler.read("missing/key")
    assert "not found" in result


@pytest.mark.asyncio
async def test_read_invalid_key() -> None:
    handler = MemoryHandler()
    result = await handler.read("INVALID_KEY")
    assert result.startswith("error:")


@pytest.mark.asyncio
async def test_read_existing_key() -> None:
    mock_read = MagicMock(return_value={
        "content": "hello world",
        "path": "memory/mykey.md",
    })
    with patch.object(nexus.vault, "read_file", mock_read):
        handler = MemoryHandler()
        result = await handler.read("mykey")
    assert "hello world" in result


# ── write ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_creates_file() -> None:
    mock_write = MagicMock()
    with patch.object(nexus.vault, "write_file", mock_write), \
         patch("nexus.tools.vault_tool._trigger_graphrag_index"):
        handler = MemoryHandler()
        result = await handler.write("newkey", "# Note\nsome content")
    assert result == "ok"
    mock_write.assert_called_once()
    call_args = mock_write.call_args
    assert call_args[0][0] == "memory/newkey.md"
    assert "# Note\nsome content" in call_args[0][1]


@pytest.mark.asyncio
async def test_write_with_tags() -> None:
    mock_write = MagicMock()
    with patch.object(nexus.vault, "write_file", mock_write), \
         patch("nexus.tools.vault_tool._trigger_graphrag_index"):
        handler = MemoryHandler()
        result = await handler.write("tagged", "content", tags=["go", "project"])
    assert result == "ok"
    written = mock_write.call_args[0][1]
    assert "tags:" in written
    assert "- go" in written


@pytest.mark.asyncio
async def test_write_without_tags() -> None:
    mock_write = MagicMock()
    with patch.object(nexus.vault, "write_file", mock_write), \
         patch("nexus.tools.vault_tool._trigger_graphrag_index"):
        handler = MemoryHandler()
        result = await handler.write("plain", "just content")
    assert result == "ok"
    assert mock_write.call_args[0][1] == "just content"


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
