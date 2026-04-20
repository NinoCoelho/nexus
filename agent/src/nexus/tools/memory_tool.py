"""Memory agent tools: memory_read, memory_write.

Persists arbitrary markdown notes to ~/.nexus/memory/<key>.md so the
agent can recall facts across sessions without bloating the vault.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..agent.llm import ToolSpec

MEMORY_DIR = Path("~/.nexus/memory").expanduser()

_KEY_RE = re.compile(r"^[a-z0-9/_-]+$")
_MAX_KEY_LEN = 128
_MAX_CONTENT_LEN = 50_000


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
        path = MEMORY_DIR / f"{key}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return "ok"
