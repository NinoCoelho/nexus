"""Coordinator agent tools: ``nexus_sessions`` + ``session_dispatch``.

Both are registered globally but refuse to run outside the coordinator
session — only the master chat may inspect other sessions or launch turns
in them. The handlers are late-bound to the ``CoordinatorService`` (set by
``app_lifespan``) via ``AgentHandlers.coordinator``, mirroring
``notify_user``.
"""

from __future__ import annotations

import json
from typing import Any

from .agent.llm import ToolSpec
from .coordinator import get_service

NEXUS_SESSIONS_TOOL = ToolSpec(
    name="nexus_sessions",
    description=(
        "Inspect projects and chat sessions across Nexus. Actions: "
        "'projects' (overview of every project — name, truncated description, "
        "chat count, latest chat; pass project_id for one project's full "
        "record including instructions), 'sessions' (recent chats, optionally "
        "filtered by project_id, title substring q, or updated_since), 'read' "
        "(the tail of one session's conversation by session_id)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["projects", "sessions", "read"],
                "description": "What to inspect.",
            },
            "project_id": {
                "type": "string",
                "description": (
                    "For action='projects', return this one project in full "
                    "(with instructions). For action='sessions', filter to it."
                ),
            },
            "session_id": {
                "type": "string",
                "description": "Session to read (for action='read').",
            },
            "q": {
                "type": "string",
                "description": "Title substring filter (for action='sessions').",
            },
            "updated_since": {
                "type": "string",
                "description": (
                    "ISO timestamp; for action='sessions', return only chats "
                    "updated at or after it. Use this to see just what changed."
                ),
            },
            "tail": {
                "type": "integer",
                "description": "How many trailing messages to include when reading (default 20, max 50).",
            },
            "include_tools": {
                "type": "boolean",
                "description": (
                    "For action='read', include tool-result messages. Off by "
                    "default — tool JSON is bulky and rarely adds meaning."
                ),
            },
        },
        "required": ["action"],
    },
)

SESSION_DISPATCH_TOOL = ToolSpec(
    name="session_dispatch",
    description=(
        "Send a message into another chat session and run its agent turn — "
        "delegation to a project chat or any session. With wait=true "
        "(default) the final reply is returned; wait=false fires and "
        "forgets. Unless the target project is in [coordinator].auto_approve, "
        "the first call is refused with needs_confirmation — confirm with "
        "the user via ask_user, then retry with confirmed=true. Disabled "
        "during proactive sweeps and for the coordinator session itself."
    ),
    parameters={
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Target session id (find ids via nexus_sessions action='sessions').",
            },
            "message": {
                "type": "string",
                "description": "Message to send as the user into that session.",
            },
            "wait": {
                "type": "boolean",
                "description": "Wait for the turn to finish and return its reply (default true).",
            },
            "confirmed": {
                "type": "boolean",
                "description": "True only after the user explicitly approved this dispatch via ask_user.",
            },
        },
        "required": ["session_id", "message"],
    },
)


def _unavailable() -> str:
    return json.dumps(
        {
            "ok": False,
            "error": "coordinator tools unavailable: coordinator not wired",
        }
    )


async def handle_nexus_sessions(args: dict[str, Any], current_session_id: str | None) -> str:
    service = get_service()
    if service is None:
        return _unavailable()
    if not service.config.enabled:
        return json.dumps({"ok": False, "error": "coordinator disabled in config"})
    if not service.is_coordinator(current_session_id):
        return json.dumps(
            {
                "ok": False,
                "error": "only the coordinator (master chat) may inspect other sessions",
            }
        )
    result = service.inspect(
        action=args.get("action", ""),
        project_id=args.get("project_id", ""),
        session_id=args.get("session_id", ""),
        q=args.get("q", ""),
        tail=int(args.get("tail", 20) or 20),
        updated_since=args.get("updated_since", "") or "",
        include_tools=bool(args.get("include_tools", False)),
    )
    return json.dumps(result)


async def handle_session_dispatch(args: dict[str, Any], current_session_id: str | None) -> str:
    service = get_service()
    if service is None:
        return _unavailable()
    if not service.config.enabled:
        return json.dumps({"ok": False, "error": "coordinator disabled in config"})
    if not service.is_coordinator(current_session_id):
        return json.dumps(
            {
                "ok": False,
                "error": "only the coordinator (master chat) may dispatch into other sessions",
            }
        )
    result = await service.dispatch(
        session_id=args.get("session_id", ""),
        message=args.get("message", ""),
        wait=bool(args.get("wait", True)),
        confirmed=bool(args.get("confirmed", False)),
    )
    return json.dumps(result)
