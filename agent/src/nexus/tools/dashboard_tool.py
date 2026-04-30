"""Data-dashboard agent tool: dashboard_manage.

Pure CRUD over the per-database `_data.md` marker file. No embedded LLM
inference — the agent itself reasons about *which* operations to seed
(via the `database-design` skill) and calls this tool to persist them.
"""

from __future__ import annotations

import json
from typing import Any

from ..agent.llm import ToolSpec

DASHBOARD_MANAGE_TOOL = ToolSpec(
    name="dashboard_manage",
    description=(
        "Manage per-database dashboards stored as `_data.md` markdown files in "
        "the vault (one per folder containing data-tables). Each dashboard "
        "holds a list of quick-action operations (chat or form), a chat "
        "session id bound to that database, and an optional title. "
        "Actions: view, set_operations, add_operation, remove_operation, "
        "set_chat_session, set_title, delete_database."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "view",
                    "set_operations",
                    "add_operation",
                    "remove_operation",
                    "set_chat_session",
                    "set_title",
                    "delete_database",
                ],
            },
            "folder": {
                "type": "string",
                "description": "Vault-relative folder of the database (e.g. 'shop'). Use '' for the vault root.",
            },
            "operations": {
                "type": "array",
                "items": {"type": "object"},
                "description": (
                    "List of operations for set_operations. Each: "
                    "{id (slug), label, kind: 'chat'|'form', prompt, table? (form only), prefill? (form only), icon?, order?}"
                ),
            },
            "operation": {
                "type": "object",
                "description": "Single operation for add_operation. Same shape as `operations` items.",
            },
            "op_id": {
                "type": "string",
                "description": "Operation id for remove_operation.",
            },
            "session_id": {
                "type": ["string", "null"],
                "description": "Chat session id for set_chat_session (null clears).",
            },
            "title": {
                "type": "string",
                "description": "Display title for set_title.",
            },
            "confirm": {
                "type": "string",
                "description": (
                    "For delete_database: must equal the folder's basename (e.g. 'shop'). "
                    "Server-side guard against accidental wipes."
                ),
            },
        },
        "required": ["action"],
    },
)


def handle_dashboard_tool(args: dict[str, Any]) -> str:
    """Dispatch the requested dashboard action and return serialized JSON."""
    from .. import vault_dashboard

    action = args.get("action", "")
    folder = args.get("folder", "")
    if not isinstance(folder, str):
        return json.dumps({"ok": False, "error": "`folder` must be a string"})

    try:
        if action == "view":
            return json.dumps({"ok": True, "dashboard": vault_dashboard.read_dashboard(folder)})

        if action == "set_operations":
            ops = args.get("operations")
            if not isinstance(ops, list):
                return json.dumps({"ok": False, "error": "`operations` (list) required"})
            patched = vault_dashboard.patch_dashboard(folder, {"operations": ops})
            return json.dumps({"ok": True, "dashboard": patched})

        if action == "add_operation":
            op = args.get("operation")
            if not isinstance(op, dict):
                return json.dumps({"ok": False, "error": "`operation` (object) required"})
            patched = vault_dashboard.upsert_operation(folder, op)
            return json.dumps({"ok": True, "dashboard": patched})

        if action == "remove_operation":
            op_id = args.get("op_id", "")
            if not op_id:
                return json.dumps({"ok": False, "error": "`op_id` required"})
            patched = vault_dashboard.delete_operation(folder, op_id)
            return json.dumps({"ok": True, "dashboard": patched})

        if action == "set_chat_session":
            sid = args.get("session_id")
            patched = vault_dashboard.set_chat_session(folder, sid if isinstance(sid, str) else None)
            return json.dumps({"ok": True, "dashboard": patched})

        if action == "set_title":
            title = args.get("title", "")
            if not title:
                return json.dumps({"ok": False, "error": "`title` required"})
            patched = vault_dashboard.patch_dashboard(folder, {"title": title})
            return json.dumps({"ok": True, "dashboard": patched})

        if action == "delete_database":
            confirm = args.get("confirm", "")
            res = vault_dashboard.delete_database(folder, confirm=confirm)
            return json.dumps({"ok": True, **res})

        return json.dumps({"ok": False, "error": f"unknown action: {action!r}"})

    except (KeyError, ValueError, FileNotFoundError, OSError) as exc:
        return json.dumps({"ok": False, "error": str(exc)})
