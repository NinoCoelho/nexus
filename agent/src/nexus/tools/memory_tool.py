"""Memory agent tools: memory_read, memory_write.

Convenience wrappers for the vault memory/ subfolder. Writes go to
vault/memory/<key>.md with YAML frontmatter and trigger full vault
indexing (FTS5, tags, links, GraphRAG). Search is via vault_search
which covers the entire vault.
"""

from __future__ import annotations

import re

import yaml

from ..agent.llm import ToolSpec

_MEMORY_PREFIX = "memory"

_KEY_RE = re.compile(r"^[a-z0-9/_-]+$")
_MAX_KEY_LEN = 128
_MAX_CONTENT_LEN = 50_000

MEMORY_READ_TOOL = ToolSpec(
    name="memory_read",
    description=(
        "Read a memory note. Keys use lowercase letters, numbers, hyphens, "
        "and slashes for namespacing (e.g. 'projects/nexus'). "
        "Memory notes live in vault/memory/ and are fully searchable via vault_search."
    ),
    parameters={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Memory key (without .md suffix).",
            },
        },
        "required": ["key"],
    },
)

MEMORY_WRITE_TOOL = ToolSpec(
    name="memory_write",
    description=(
        "Write or overwrite a memory note. Use to persist facts, user "
        "preferences, or project context across sessions. Content is plain "
        "markdown. Overwrites existing content at the key."
    ),
    parameters={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Memory key (without .md suffix).",
            },
            "content": {
                "type": "string",
                "description": "Markdown content to store.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tags for categorization.",
            },
        },
        "required": ["key", "content"],
    },
)


def _validate_key(key: str) -> str | None:
    if not key:
        return "error: key must not be empty"
    if len(key) > _MAX_KEY_LEN:
        return f"error: key too long (max {_MAX_KEY_LEN} chars)"
    if ".." in key:
        return "error: key must not contain '..'"
    if not _KEY_RE.match(key):
        return "error: key must match [a-z0-9/_-]+"
    return None


def _build_content(content: str, tags: list[str] | None) -> str:
    if not tags:
        return content
    fm = {"tags": tags}
    fm_str = yaml.dump(fm, default_flow_style=False).strip()
    return f"---\n{fm_str}\n---\n{content}"


class MemoryHandler:
    async def read(self, key: str) -> str:
        err = _validate_key(key)
        if err:
            return err
        from .. import vault

        try:
            result = vault.read_file(f"{_MEMORY_PREFIX}/{key}.md")
            return result["content"]
        except FileNotFoundError:
            return f"error: memory key {key!r} not found"

    async def write(
        self, key: str, content: str, tags: list[str] | None = None
    ) -> str:
        err = _validate_key(key)
        if err:
            return err
        if len(content) > _MAX_CONTENT_LEN:
            return f"error: content too large (max {_MAX_CONTENT_LEN} chars)"
        from .. import vault

        full = _build_content(content, tags)
        path = f"{_MEMORY_PREFIX}/{key}.md"
        vault.write_file(path, full)
        from .vault_tool import _trigger_graphrag_index

        _trigger_graphrag_index(path, full)
        return "ok"
