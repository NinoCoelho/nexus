"""Memory agent tools: memory_read, memory_write, memory_recall.

Persists arbitrary markdown notes to ~/.nexus/memory/<key>.md so the
agent can recall facts across sessions without bloating the vault.

Recall is backed by loom.store.memory.MemoryStore which uses BM25 +
salience + recency ranking (no embedding provider required — the store
defaults to pure BM25+salience when no EmbeddingProvider is given).
"""

from __future__ import annotations

import re
from pathlib import Path

from ..agent.llm import ToolSpec

MEMORY_DIR = Path("~/.nexus/memory").expanduser()
# loom MemoryStore uses its own subdirectory and index so it doesn't
# conflict with the plain .md files that memory_read/write manage.
# Plain files under MEMORY_DIR are the canonical readable format;
# the loom store is the searchable index used by memory_recall.
_LOOM_STORE_DIR = Path("~/.nexus/memory/.loom").expanduser()
_MEMORY_INDEX = _LOOM_STORE_DIR / "_index.sqlite"

_KEY_RE = re.compile(r"^[a-z0-9/_-]+$")
_MAX_KEY_LEN = 128
_MAX_CONTENT_LEN = 50_000

# Module-level MemoryStore singleton — lazily initialised on first use.
# We don't create it at import time to avoid touching the filesystem unless
# the tool is actually invoked. Guarded by the same thread used by FastAPI's
# asyncio loop (sqlite check_same_thread=False in loom's store).
_store: "MemoryStore | None" = None  # noqa: F821 (forward ref, imported lazily)


def _get_store() -> "MemoryStore":  # noqa: F821
    global _store
    if _store is None:
        from loom.store.memory import MemoryStore

        # No EmbeddingProvider — loom falls back to pure BM25+salience+recency.
        # This is intentional: keeps the tool fully offline and avoids any
        # network dependency. Semantic recall improves naturally as salience
        # signals accumulate with use.
        _LOOM_STORE_DIR.mkdir(parents=True, exist_ok=True)
        _store = MemoryStore(_LOOM_STORE_DIR, _MEMORY_INDEX)
    return _store


MEMORY_READ_TOOL = ToolSpec(
    name="memory_read",
    description=(
        "Read a memory note from ~/.nexus/memory/<key>.md. "
        "Use to recall facts about the user, ongoing projects, or preferences you've saved previously. "
        "Keys use lowercase letters, numbers, hyphens, and slashes (for namespacing, e.g. 'projects/nexus')."
    ),
    parameters={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Memory key (path under ~/.nexus/memory/, without .md).",
            },
        },
        "required": ["key"],
    },
)

MEMORY_WRITE_TOOL = ToolSpec(
    name="memory_write",
    description=(
        "Write or overwrite a memory note at ~/.nexus/memory/<key>.md. "
        "Use to persist facts, user preferences, or project context across sessions. "
        "Content is plain markdown. Overwrites existing content at the key."
    ),
    parameters={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Memory key (path under ~/.nexus/memory/, without .md).",
            },
            "content": {
                "type": "string",
                "description": "Markdown content to store.",
            },
        },
        "required": ["key", "content"],
    },
)

MEMORY_RECALL_TOOL = ToolSpec(
    name="memory_recall",
    description=(
        "Semantically recall stored memories relevant to a natural-language query. "
        "Use when the user references past events, decisions, or saved context without a specific key. "
        "Returns a ranked list of matching memory snippets."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language query describing what to recall.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return (default 5).",
            },
        },
        "required": ["query"],
    },
)


def _validate_key(key: str) -> str | None:
    """Return an error string if invalid, else None."""
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
    """Stateless handler — safe to instantiate per call."""

    async def read(self, key: str) -> str:
        err = _validate_key(key)
        if err:
            return err
        path = MEMORY_DIR / f"{key}.md"
        if not path.exists():
            return f"error: memory key {key!r} not found"
        return path.read_text(encoding="utf-8")

    async def write(self, key: str, content: str) -> str:
        err = _validate_key(key)
        if err:
            return err
        if len(content) > _MAX_CONTENT_LEN:
            return f"error: content too large (max {_MAX_CONTENT_LEN} chars)"
        # Write the flat markdown file (backward compat).
        path = MEMORY_DIR / f"{key}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        # Also index into loom MemoryStore so memory_recall can find it.
        try:
            store = _get_store()
            await store.write(key, content)
        except Exception:
            # Don't fail the write if loom indexing errors — the file is saved.
            pass
        return "ok"

    async def recall(self, query: str, limit: int = 5) -> str:
        store = _get_store()
        # Check if the store has any entries before querying.
        try:
            entries = await store.list_entries(limit=1)
        except Exception:
            return "(no memories stored yet)"
        if not entries:
            return "(no memories stored yet)"

        try:
            hits = await store.recall(query=query, limit=limit)
        except Exception as exc:
            return f"error: recall failed — {exc}"

        if not hits:
            return "(no matching memories found)"

        sections: list[str] = []
        for hit in hits:
            preview = hit.preview[:200].strip()
            score_str = f"{hit.score:.3f}"
            sections.append(f"**{hit.key}** (score: {score_str})\n\n{preview}")

        return "\n\n---\n\n".join(sections)
