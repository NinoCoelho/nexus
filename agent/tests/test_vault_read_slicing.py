"""Pagination + auto-truncation for vault_read.

Regression target: a single 1MB CSV returned by vault_read poisoned a session
because the agent had no way to page or to know the file was huge — the tool
just dumped the whole thing. These tests pin down the new slicing knobs and
the default 64KB byte cap.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus import vault
from nexus.tools.vault_tool import handle_vault_tool


@pytest.fixture
def isolated_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(vault, "_VAULT_ROOT", tmp_path)
    return tmp_path


def _read(args: dict) -> dict:
    return json.loads(handle_vault_tool("vault_read", args))


def test_default_cap_truncates_large_file(isolated_vault: Path) -> None:
    body = "x" * (200 * 1024)  # 200KB
    (isolated_vault / "big.txt").write_text(body)
    out = _read({"path": "big.txt"})
    assert out["ok"]
    assert out["truncated"] is True
    assert out["size"] == len(body)
    assert len(out["content"]) == 64 * 1024  # default cap
    assert "hint" in out
    assert out["slice"]["next_offset"] == 64 * 1024


def test_small_file_not_truncated(isolated_vault: Path) -> None:
    (isolated_vault / "small.md").write_text("hello")
    out = _read({"path": "small.md"})
    assert out["ok"]
    assert out.get("truncated") is not True
    assert out["content"] == "hello"


def test_head_slice(isolated_vault: Path) -> None:
    (isolated_vault / "lines.txt").write_text("\n".join(f"line{i}" for i in range(100)))
    out = _read({"path": "lines.txt", "head": 5})
    assert out["truncated"] is True
    assert out["content"].splitlines() == [f"line{i}" for i in range(5)]
    assert out["slice"]["mode"] == "head"
    assert out["slice"]["lines_returned"] == 5
    assert out["slice"]["total_lines"] == 100


def test_tail_slice(isolated_vault: Path) -> None:
    (isolated_vault / "lines.txt").write_text("\n".join(f"line{i}" for i in range(100)))
    out = _read({"path": "lines.txt", "tail": 3})
    assert out["truncated"] is True
    # tail of 3 from a 100-line file (no trailing newline) → last 3 entries
    assert "line99" in out["content"]
    assert out["slice"]["mode"] == "tail"


def test_byte_range_paging(isolated_vault: Path) -> None:
    body = "abcdefghij" * 100  # 1000 bytes
    (isolated_vault / "data.bin").write_text(body)
    page1 = _read({"path": "data.bin", "offset": 0, "limit": 100})
    assert page1["truncated"] is True
    assert page1["content"] == body[:100]
    next_off = page1["slice"]["next_offset"]
    assert next_off == 100
    page2 = _read({"path": "data.bin", "offset": next_off, "limit": 100})
    assert page2["content"] == body[100:200]


def test_explicit_limit_overrides_default_cap(isolated_vault: Path) -> None:
    body = "y" * (300 * 1024)
    (isolated_vault / "big.txt").write_text(body)
    out = _read({"path": "big.txt", "limit": 1024})
    assert len(out["content"]) == 1024
    assert out["truncated"] is True


def test_frontmatter_skipped_on_truncated_read(isolated_vault: Path) -> None:
    """Slices may cut YAML in half — never claim parsed frontmatter for a partial."""
    body = "---\ntitle: x\ntags: [a, b]\n---\n" + ("body line\n" * 10000)
    (isolated_vault / "doc.md").write_text(body)
    out = _read({"path": "doc.md"})  # default cap kicks in
    assert out["truncated"] is True
    assert "frontmatter" not in out
    # Full read (above the cap) returns the parsed frontmatter
    full = vault.read_file("doc.md", limit=10**9)
    assert full.get("truncated") is not True
    assert full["frontmatter"]["title"] == "x"
