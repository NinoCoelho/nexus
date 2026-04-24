"""dispatch_card agent tool.

Spawns a chat session seeded from a vault file or kanban card. Mirrors the
``/vault/dispatch`` HTTP endpoint so the agent can start a sub-task without
the user navigating to it. Returns the new session id (and seed message for
``chat`` mode) so the caller can link to it.

Modes
-----
- ``background``: starts the agent server-side; the linked card flips to
  ``running`` and resolves to ``done``/``failed`` when the turn completes.
  Requires a ``card_id``.
- ``chat``: returns ``seed_message`` so the orchestrating UI can prefill it
  into a chat input. Does **not** start a turn.
- ``chat-hidden``: same as ``chat`` but the seed is marked invisible to
  the chat bubble renderer.
"""

from __future__ import annotations

import json
from typing import Any

from ..agent.llm import ToolSpec

DISPATCH_CARD_TOOL = ToolSpec(
    name="dispatch_card",
    description=(
        "Spawn a chat session seeded from a vault file or kanban card. "
        "Use mode='background' to run the sub-task server-side and stamp "
        "running/done status onto the card. Use mode='chat' or 'chat-hidden' "
        "to return a seed for the UI to start a session interactively. "
        "Background dispatch requires card_id."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Vault-relative path to the file or kanban board.",
            },
            "card_id": {
                "type": "string",
                "description": "Card id when dispatching from a kanban board (required for background mode).",
            },
            "mode": {
                "type": "string",
                "enum": ["chat", "background", "chat-hidden"],
                "description": "Dispatch mode (default: 'background' if card_id is given, else 'chat').",
            },
        },
        "required": ["path"],
    },
)


async def handle_dispatch_card_tool(args: dict[str, Any], dispatcher: Any) -> str:
    """Tool handler. ``dispatcher`` is the late-bound async callable from
    AgentHandlers.dispatcher; if not wired, returns a structured error so the
    agent can fall back to plain kanban_manage operations.
    """
    if dispatcher is None:
        return json.dumps({
            "ok": False,
            "error": "dispatch_card unavailable: dispatcher not wired (server must call agent._handlers.dispatcher = ...)",
        })
    path = args.get("path", "")
    if not path:
        return json.dumps({"ok": False, "error": "`path` is required"})
    card_id = args.get("card_id") or None
    mode = args.get("mode") or ("background" if card_id else "chat")
    if mode not in ("chat", "background", "chat-hidden"):
        return json.dumps({"ok": False, "error": f"invalid mode: {mode!r}"})
    if mode == "background" and not card_id:
        return json.dumps({"ok": False, "error": "background mode requires `card_id`"})
    try:
        result = await dispatcher(path=path, card_id=card_id, mode=mode)
    except (KeyError, ValueError, FileNotFoundError) as exc:
        return json.dumps({"ok": False, "error": str(exc)})
    return json.dumps({"ok": True, **result})
