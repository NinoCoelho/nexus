"""NexusVaultProvider satisfies loom's VaultProvider protocol and
roundtrips through Nexus's vault module."""

from __future__ import annotations

from pathlib import Path

import pytest

from loom.store.vault import VaultProvider

from nexus.vault_provider import NexusVaultProvider


@pytest.fixture
def isolated_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point nexus.vault and nexus.vault_search at a tmp dir so the
    test doesn't stomp on ~/.nexus/vault."""
    from nexus import vault, vault_search

    monkeypatch.setattr(vault, "_VAULT_ROOT", tmp_path)
    monkeypatch.setattr(vault_search, "_VAULT_ROOT", tmp_path)
    monkeypatch.setattr(vault_search, "_INDEX_PATH", tmp_path / "_index.sqlite")
    return tmp_path


def test_conforms_to_protocol() -> None:
    provider = NexusVaultProvider()
    assert isinstance(provider, VaultProvider)


async def test_write_read_roundtrip(isolated_vault: Path) -> None:
    provider = NexusVaultProvider()
    await provider.write("notes/hello.md", "# Hello\n\nworld")
    content = await provider.read("notes/hello.md")
    assert "Hello" in content
    assert "world" in content


async def test_list_filters_by_prefix(isolated_vault: Path) -> None:
    provider = NexusVaultProvider()
    await provider.write("a/one.md", "one")
    await provider.write("b/two.md", "two")
    assert "a/one.md" in await provider.list("a")
    assert "b/two.md" not in await provider.list("a")


async def test_write_applies_metadata_frontmatter(isolated_vault: Path) -> None:
    provider = NexusVaultProvider()
    await provider.write(
        "doc.md", "body text", metadata={"title": "Doc", "tags": ["x"]}
    )
    content = await provider.read("doc.md")
    assert content.startswith("---\n")
    assert "title: Doc" in content
    assert "body text" in content


async def test_delete_removes_file(isolated_vault: Path) -> None:
    provider = NexusVaultProvider()
    await provider.write("gone.md", "bye")
    await provider.delete("gone.md")
    with pytest.raises(FileNotFoundError):
        await provider.read("gone.md")
