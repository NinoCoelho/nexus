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

VAULT_TOOLS = [VAULT_LIST_TOOL, VAULT_READ_TOOL, VAULT_WRITE_TOOL]


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

        return json.dumps({"ok": False, "error": f"unknown vault tool: {name!r}"})

    except (ValueError, FileNotFoundError, OSError) as exc:
        return json.dumps({"ok": False, "error": str(exc)})
