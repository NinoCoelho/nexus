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
    description="Read a file from the vault. Returns content and parsed frontmatter if present.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path within the vault."},
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

VAULT_TOOLS = [VAULT_LIST_TOOL, VAULT_READ_TOOL, VAULT_WRITE_TOOL, VAULT_SEARCH_TOOL]


def handle_vault_tool(name: str, args: dict[str, Any]) -> str:
    from .. import vault

    try:
        if name == "vault_list":
            path = args.get("path", "")
            entries = vault.list_tree()
            if path:
                entries = [e for e in entries if e.path.startswith(path.rstrip("/") + "/") or e.path == path]
            return json.dumps({"ok": True, "entries": [{"path": e.path, "type": e.type, "size": e.size} for e in entries]})

        if name == "vault_read":
            path = args.get("path", "")
            if not path:
                return json.dumps({"ok": False, "error": "`path` is required"})
            result = vault.read_file(path)
            return json.dumps({"ok": True, **result})

        if name == "vault_write":
            path = args.get("path", "")
            content = args.get("content", "")
            if not path:
                return json.dumps({"ok": False, "error": "`path` is required"})
            vault.write_file(path, content)
            return json.dumps({"ok": True})

        if name == "vault_search":
            from .. import vault_search
            query = args.get("query", "")
            limit = int(args.get("limit", 10))
            if not query:
                return json.dumps({"ok": False, "error": "`query` is required"})
            if vault_search.is_empty():
                vault_search.rebuild_from_disk()
            results = vault_search.search(query, limit=limit)
            return json.dumps({"ok": True, "results": results})

        return json.dumps({"ok": False, "error": f"unknown vault tool: {name!r}"})

    except (ValueError, FileNotFoundError, OSError) as exc:
        return json.dumps({"ok": False, "error": str(exc)})
