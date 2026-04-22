"""Adapter exposing Nexus's vault as a :class:`loom.store.vault.VaultProvider`.

Loom's ``VaultProvider`` protocol is the lingua franca for vault-like
backends: any Loom-based agent can plug a provider into
``loom.tools.vault``'s ``read`` / ``write`` / ``search`` / ``list`` /
``delete`` tools. Nexus's vault has richer semantics than the default
``FilesystemVaultProvider`` (FTS5 index, kanban boards, backlinks, tag
graph), so we adapt the existing module API rather than swap it out.

Callers inside Nexus should keep using :mod:`nexus.vault` directly —
this class only exists so *external* Loom consumers can share the
Nexus vault when embedded (e.g. a sibling Loom agent running in the
same process).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from . import vault, vault_search


class NexusVaultProvider:
    """Implements :class:`loom.store.vault.VaultProvider` on top of the
    existing :mod:`nexus.vault` module + :mod:`nexus.vault_search` FTS5
    index. All methods are async for protocol conformance; the
    underlying calls are synchronous and fast (local FS + SQLite), so
    we don't bother pushing them to a thread pool."""

    @property
    def root(self) -> Path:
        return vault._VAULT_ROOT

    async def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = vault_search.search(query, limit=limit)
        return [
            {"path": r["path"], "snippet": r["snippet"], "score": r["score"]}
            for r in rows
        ]

    async def search_scoped(
        self, query: str, path_prefix: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        rows = vault_search.search(query, limit=limit * 3)
        if path_prefix:
            rows = [r for r in rows if r["path"].startswith(path_prefix)]
        return [
            {"path": r["path"], "snippet": r["snippet"], "score": r["score"]}
            for r in rows[:limit]
        ]

    async def read(self, path: str) -> str:
        return vault.read_file(path)["content"]

    async def write(
        self, path: str, content: str, metadata: dict | None = None
    ) -> None:
        if metadata:
            fm = yaml.dump(metadata, default_flow_style=False).strip()
            content = f"---\n{fm}\n---\n{content}"
        vault.write_file(path, content)

    async def list(self, prefix: str = "") -> list[str]:
        entries = vault.list_tree()
        paths = [e.path for e in entries if e.type == "file"]
        if prefix:
            paths = [p for p in paths if p.startswith(prefix)]
        return paths

    async def delete(self, path: str) -> None:
        vault.delete(path)

    def read_frontmatter(self, path: str) -> dict[str, Any]:
        result = vault.read_file(path)
        fm = result.get("frontmatter")
        if fm is not None:
            return fm
        return {}

    def update_frontmatter(self, path: str, updates: dict[str, Any]) -> None:
        result = vault.read_file(path)
        content = result["content"]
        fm = result.get("frontmatter") or {}
        fm.update(updates)
        body = result.get("body", content)
        fm_str = yaml.dump(fm, default_flow_style=False).strip()
        full = f"---\n{fm_str}\n---\n{body}"
        vault.write_file(path, full)
