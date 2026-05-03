"""Vault agent tools: vault_read, vault_write, vault_list."""

from __future__ import annotations

import json
from typing import Any

from ..agent.llm import ToolSpec

VAULT_LIST_TOOL = ToolSpec(
    name="vault_list",
    description="List files and folders in the vault (or a subdirectory).",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Subdirectory to list (default: root).",
            },
        },
    },
)

VAULT_READ_TOOL = ToolSpec(
    name="vault_read",
    description=(
        "Read a file from the vault. Returns content and parsed frontmatter if present.\n\n"
        "Files larger than 64KB are auto-truncated. Use `head`/`tail` (line counts) "
        "or `offset`/`limit` (byte ranges) to read specific slices of large files. "
        "When the result contains `truncated: true`, the response also includes a "
        "`slice` object with metadata (lines_returned, next_offset, …) so you can "
        "page through the file. Reading a 1MB CSV in one shot will pollute the "
        "conversation context — always slice large files."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path within the vault."},
            "head": {
                "type": "integer",
                "description": "Return only the first N lines. Mutually exclusive with tail/offset/limit.",
            },
            "tail": {
                "type": "integer",
                "description": "Return only the last N lines. Mutually exclusive with head/offset/limit.",
            },
            "offset": {
                "type": "integer",
                "description": "Byte offset to start reading from (0-based). Pair with `limit`.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum bytes to return (default: 64KB cap when no slice args given).",
            },
        },
        "required": ["path"],
    },
)

VAULT_WRITE_TOOL = ToolSpec(
    name="vault_write",
    description="Write (create or overwrite) a file in the vault.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path within the vault."},
            "content": {"type": "string", "description": "UTF-8 text content to write."},
        },
        "required": ["path", "content"],
    },
)

VAULT_SEARCH_TOOL = ToolSpec(
    name="vault_search",
    description="Full-text search across all vault notes. Returns matching file paths and snippets.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "limit": {"type": "integer", "description": "Max results to return (default 10)."},
        },
        "required": ["query"],
    },
)

VAULT_TAGS_TOOL = ToolSpec(
    name="vault_tags",
    description=(
        "List all tags in the vault (with file counts), or list files for a specific tag. "
        "Omit `tag` to get the full tag index; provide `tag` to get files with that tag."
    ),
    parameters={
        "type": "object",
        "properties": {
            "tag": {
                "type": "string",
                "description": "Tag name to look up. Omit to list all tags.",
            },
        },
    },
)

VAULT_BACKLINKS_TOOL = ToolSpec(
    name="vault_backlinks",
    description="List all vault files that link to the given file path.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path within the vault."},
        },
        "required": ["path"],
    },
)

VAULT_TOOLS = [VAULT_LIST_TOOL, VAULT_READ_TOOL, VAULT_WRITE_TOOL, VAULT_SEARCH_TOOL, VAULT_TAGS_TOOL, VAULT_BACKLINKS_TOOL]

VAULT_SEMANTIC_SEARCH_TOOL = ToolSpec(
    name="vault_semantic_search",
    description=(
        "Semantic search across vault notes using vector embeddings and entity graph. "
        "Returns relevant chunks with source file paths and related entities. "
        "More powerful than vault_search for conceptual queries."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural language search query."},
            "limit": {"type": "integer", "description": "Max results to return (default 10)."},
        },
        "required": ["query"],
    },
)


def handle_vault_tool(name: str, args: dict[str, Any]) -> str:
    """Dispatch a vault tool by name and return serialized JSON.

    Args:
        name: Tool name (``vault_list``, ``vault_read``, ``vault_write``,
              ``vault_search``, ``vault_tags``, ``vault_backlinks``,
              ``vault_semantic_search``).
        args: Tool parameters as defined by the corresponding ToolSpec.

    Returns:
        JSON with ``{"ok": true, ...}`` on success or ``{"ok": false, "error": ...}``.
        Non-serializable values (e.g. ``datetime.date`` from YAML frontmatter)
        are stringified via ``default=str`` to avoid crashing the SSE stream
        with a serialization exception.
    """
    from .. import vault

    # json.dumps can't handle datetime.date / datetime.datetime (YAML
    # frontmatter auto-coerces ISO-like values). default=str stringifies
    # them instead of raising — crashes here previously killed the SSE
    # stream mid-flight and surfaced as ERR_INCOMPLETE_CHUNKED_ENCODING
    # on the client.
    def _dumps(obj: dict) -> str:
        return json.dumps(obj, default=str)

    try:
        if name == "vault_list":
            path = args.get("path", "")
            entries = vault.list_tree()
            if path:
                entries = [e for e in entries if e.path.startswith(path.rstrip("/") + "/") or e.path == path]
            return _dumps({"ok": True, "entries": [{"path": e.path, "type": e.type, "size": e.size} for e in entries]})

        if name == "vault_read":
            path = args.get("path", "")
            if not path:
                return _dumps({"ok": False, "error": "`path` is required"})
            head = args.get("head")
            tail = args.get("tail")
            offset = int(args.get("offset", 0) or 0)
            limit = args.get("limit")
            # Default cap: when the agent didn't request a specific slice we
            # still avoid dumping multi-MB files into the conversation. 64KB
            # ≈ ~16k tokens, which is a sane budget for a single tool result.
            DEFAULT_BYTE_CAP = 64 * 1024
            if head is None and tail is None and limit is None and offset == 0:
                limit = DEFAULT_BYTE_CAP
            result = vault.read_file(
                path,
                offset=offset,
                limit=int(limit) if limit is not None else None,
                head=int(head) if head is not None else None,
                tail=int(tail) if tail is not None else None,
            )
            if result.get("truncated"):
                result["hint"] = (
                    "Output truncated. Use `head=N`/`tail=N` for line slices, "
                    "or `offset`/`limit` (bytes) to page. Field `slice.next_offset` "
                    "indicates where to resume."
                )
            return _dumps({"ok": True, **result})

        if name == "vault_write":
            path = args.get("path", "")
            content = args.get("content", "")
            if not path:
                return _dumps({"ok": False, "error": "`path` is required"})
            vault.write_file(path, content)
            _trigger_graphrag_index(path, content)
            return _dumps({"ok": True})

        if name == "vault_search":
            from .. import vault_search
            query = args.get("query", "")
            limit = int(args.get("limit", 10))
            if not query:
                return _dumps({"ok": False, "error": "`query` is required"})
            if vault_search.is_empty():
                vault_search.rebuild_from_disk()
            results = vault_search.search(query, limit=limit)
            return _dumps({"ok": True, "results": results})

        if name == "vault_tags":
            from .. import vault_index
            tag = args.get("tag", "")
            if vault_index.is_empty():
                vault_index.rebuild_from_disk()
            if tag:
                files = vault_index.files_with_tag(tag)
                return _dumps({"ok": True, "tag": tag, "files": files})
            tags = vault_index.list_tags()
            return _dumps({"ok": True, "tags": tags})

        if name == "vault_backlinks":
            from .. import vault_index
            path = args.get("path", "")
            if not path:
                return _dumps({"ok": False, "error": "`path` is required"})
            if vault_index.is_empty():
                vault_index.rebuild_from_disk()
            links = vault_index.backlinks(path)
            return _dumps({"ok": True, "path": path, "backlinks": links})

        if name == "vault_semantic_search":
            return _handle_semantic_search(args, _dumps)

        return _dumps({"ok": False, "error": f"unknown vault tool: {name!r}"})

    except (ValueError, FileNotFoundError, OSError) as exc:
        return _dumps({"ok": False, "error": str(exc)})


def _get_graphrag_engine() -> Any:
    from ..agent.graphrag_manager import get_engine
    return get_engine()


async def _handle_semantic_search(args: dict[str, Any], _dumps: Any) -> str:
    query = args.get("query", "")
    limit = int(args.get("limit", 10))
    if not query:
        return _dumps({"ok": False, "error": "`query` is required"})
    engine = _get_graphrag_engine()
    if engine is None:
        return _dumps({"ok": False, "error": "GraphRAG not enabled or not initialized"})
    try:
        results = await engine.retrieve(query, top_k=limit)
    except ValueError as exc:
        # Loom's _batch_cosine calls np.array(vectors, ...) which raises
        # "inhomogeneous shape" when stored embeddings have mixed
        # dimensions — caused by switching embedding models without
        # re-indexing. Surface an actionable error instead of the raw
        # numpy traceback so the agent stops retrying blindly.
        msg = str(exc)
        if "inhomogeneous" in msg or "setting an array element with a sequence" in msg:
            return _dumps({
                "ok": False,
                "error": (
                    "Vault embeddings have mixed dimensions — likely after an "
                    "embedding-model change. Re-index the vault before calling "
                    "vault_semantic_search again. Use `vault_search` (FTS) "
                    "for now."
                ),
                "recovery": "rebuild_vault_index",
            })
        raise
    return _dumps({
        "ok": True,
        "results": [
            {
                "source_path": r.source_path,
                "heading": r.heading,
                "snippet": r.content[:500],
                "score": round(r.score, 4),
                "source": r.source,
                "related_entities": r.related_entities,
            }
            for r in results
        ],
    })


def _trigger_graphrag_index(path: str, content: str) -> None:
    import asyncio
    from ..agent.graphrag_manager import schedule_index
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    schedule_index(path, content)
