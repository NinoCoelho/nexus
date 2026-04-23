"""Memory agent tools: memory_read, memory_write.

Delegates to :class:`loom.store.memory.MemoryStore` which handles
vault-backed storage with date-based directory hierarchy, FTS5 search,
salience/recency ranking, and automatic GraphRAG indexing when enabled.
"""

from __future__ import annotations

import re

from ..agent.llm import ToolSpec

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


class MemoryHandler:
    def __init__(self) -> None:
        self._store: object | None = None

    def _get_store(self) -> object:
        if self._store is None:
            from ..agent.memory import get_memory_store
            self._store = get_memory_store()
        return self._store

    async def read(self, key: str) -> str:
        err = _validate_key(key)
        if err:
            return err
        store = self._get_store()
        entry = await store.read(key)
        if entry is None:
            return f"error: memory key {key!r} not found"
        return entry.content

    async def write(
        self, key: str, content: str, tags: list[str] | None = None
    ) -> str:
        err = _validate_key(key)
        if err:
            return err
        if len(content) > _MAX_CONTENT_LEN:
            return f"error: content too large (max {_MAX_CONTENT_LEN} chars)"
        store = self._get_store()
        await store.write(key, content, category="notes", tags=tags or [])
        return "ok"
