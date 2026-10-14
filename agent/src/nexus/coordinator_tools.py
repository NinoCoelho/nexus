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


BOT_CONFIG_TOOL = ToolSpec(
    name="bot_config",
    description=(
        "Create, read, update and delete bots (projects) — THE way to manage "
        "bots; never write to the sessions database directly. Actions: "
        "'list' (every bot: id, @handle, name, persona preview), 'get' (one "
        "bot's full record via bot id or handle), 'create' (name required; "
        "persona/instructions/description optional; assigns a unique @handle "
        "and creates its vault folder), 'update' (patch persona/instructions/"
        "name/description), 'delete' (chats: archive default | delete | keep). "
        "create/update/delete need the user's ok unless allowed by "
        "[coordinator].auto_approve — a first call without confirmed=true "
        "returns needs_confirmation; confirm via ask_user, then retry."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "get", "create", "update", "delete"],
                "description": "What to do.",
            },
            "bot": {
                "type": "string",
                "description": "Bot id or @handle (get/update/delete).",
            },
            "name": {
                "type": "string",
                "description": "Bot name (create required; update renames, @handle follows).",
            },
            "persona": {
                "type": "string",
                "description": "How the bot behaves (create/update).",
            },
            "instructions": {
                "type": "string",
                "description": "Standing instructions (create/update).",
            },
            "description": {
                "type": "string",
                "description": "Short description (create/update).",
            },
            "chats": {
                "type": "string",
                "enum": ["archive", "delete", "keep"],
                "description": "What happens to the bot's chats on delete (default archive).",
            },
            "confirmed": {
                "type": "boolean",
                "description": "True only after the user explicitly approved via ask_user.",
            },
        },
        "required": ["action"],
    },
)

TEAMS_TOOL = ToolSpec(
    name="teams",
    description=(
        "Manage team chats — group conversations where several bots "
        "collaborate (the first member is the lead and answers messages that "
        "@mention nobody; @mention any member to address them directly). "
        "Actions: 'list' (existing teams with member handles), 'create' "
        "(name + members as bot ids or @handles — first is the lead; mode "
        "'lead' = lead answers unless mentioned, 'round' = everyone answers "
        "in turn), 'update' (rename/change members/mode by session_id). "
        "Creation is low-risk (a new chat) but confirm with the user when "
        "they asked for something specific."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "create", "update"],
                "description": "What to do.",
            },
            "name": {
                "type": "string",
                "description": "Team name (create; update renames).",
            },
            "members": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Bot ids or @handles; FIRST is the lead (create/update).",
            },
            "mode": {
                "type": "string",
                "enum": ["lead", "round"],
                "description": "lead = lead answers unless someone is @mentioned; round = every member answers (default lead).",
            },
            "session_id": {
                "type": "string",
                "description": "Team chat session to update (find via list).",
            },
        },
        "required": ["action"],
    },
)


async def handle_teams(args: dict[str, Any], current_session_id: str | None) -> str:
    service = get_service()
    if service is None:
        return _unavailable()
    if not service.config.enabled:
        return json.dumps({"ok": False, "error": "coordinator disabled in config"})
    if not service.is_coordinator(current_session_id):
        return json.dumps(
            {"ok": False, "error": "only the coordinator (master chat) may manage teams"}
        )
    action = args.get("action", "")
    store = service.store
    try:
        if action == "list":
            rows = store.list(200, 0)
            by_handle: dict[str, str] = {}
            pstore = _bot_store()
            for s in pstore.list(limit=200):
                by_handle[s.id] = s.handle
            teams = []
            for row in rows:
                if not row.team:
                    continue
                try:
                    team = json.loads(row.team)
                except (TypeError, json.JSONDecodeError):
                    continue
                members = [
                    {"id": m, "handle": by_handle.get(m, "?")} for m in team.get("members", [])
                ]
                teams.append(
                    {
                        "session_id": row.id,
                        "title": row.title,
                        "name": team.get("name", row.title),
                        "members": members,
                        "mode": team.get("mode", "lead"),
                    }
                )
            return json.dumps({"ok": True, "teams": teams})

        if action == "create":
            from .server.routes.bots import _create_team_impl

            # Accept ids or @handles — resolve handles to ids first.
            pstore = _bot_store()
            members: list[str] = []
            for ref in args.get("members") or []:
                proj, err = _find_bot(pstore, str(ref))
                if err:
                    return json.dumps({"ok": False, "error": err})
                members.append(proj.id)
            try:
                result = await _create_team_impl(
                    store,
                    {
                        "name": args.get("name", ""),
                        "members": members,
                        "mode": args.get("mode", "lead"),
                    },
                )
                return json.dumps({"ok": True, **result})
            except Exception as exc:  # noqa: BLE001 — tool result, not a crash
                detail = getattr(exc, "detail", None) or str(exc)
                return json.dumps({"ok": False, "error": f"create failed: {detail}"})

        if action == "update":
            session_id = (args.get("session_id") or "").strip()
            if not session_id:
                return json.dumps({"ok": False, "error": "session_id is required"})
            if store.get_team(session_id) is None:
                return json.dumps({"ok": False, "error": "session has no team"})
            body: dict[str, Any] = {}
            if args.get("name"):
                body["name"] = args["name"]
            if args.get("mode"):
                body["mode"] = args["mode"]
            if args.get("members") is not None:
                pstore = _bot_store()
                resolved = []
                for ref in args.get("members") or []:
                    proj, err = _find_bot(pstore, str(ref))
                    if err:
                        return json.dumps({"ok": False, "error": err})
                    resolved.append(proj.id)
                body["members"] = resolved
            try:
                from .server.routes.bots import update_team as _update_team_route

                result = await _update_team_route(session_id, body, store)
                return json.dumps({"ok": True, **result})
            except Exception as exc:  # noqa: BLE001 — tool result, not a crash
                detail = getattr(exc, "detail", None) or str(exc)
                return json.dumps({"ok": False, "error": f"update failed: {detail}"})

        return json.dumps({"ok": False, "error": f"unknown action: {action}"})
    except Exception as exc:  # noqa: BLE001 — tool result, not a crash
        return json.dumps({"ok": False, "error": f"teams failed: {exc}"})


def _bot_store():
    from .home import sessions_db
    from .server.project_store import ProjectStore

    return ProjectStore(sessions_db())


def _find_bot(store: Any, ref: str):
    ref = (ref or "").strip().lstrip("@").lower()
    if not ref:
        return None, "bot is required (id or @handle)"
    proj = store.get(ref) or store.get_by_handle(ref)
    if proj is None:
        return None, f"bot not found: {ref}"
    return proj, None


async def handle_bot_config(args: dict[str, Any], current_session_id: str | None) -> str:
    service = get_service()
    if service is None:
        return _unavailable()
    if not service.config.enabled:
        return json.dumps({"ok": False, "error": "coordinator disabled in config"})
    if not service.is_coordinator(current_session_id):
        return json.dumps(
            {
                "ok": False,
                "error": "only the coordinator (master chat) may change bot configuration",
            }
        )
    action = args.get("action", "")
    try:
        store = _bot_store()
        if action == "list":
            out = []
            for s in store.list(limit=200):
                out.append(
                    {
                        "id": s.id,
                        "handle": s.handle,
                        "name": s.name,
                        "description": s.description[:200],
                        "persona_preview": (s.persona or "")[:200],
                        "session_count": s.session_count,
                    }
                )
            return json.dumps({"ok": True, "bots": out})

        if action == "create":
            name = (args.get("name") or "").strip()
            if not name:
                return json.dumps({"ok": False, "error": "name is required"})
            fields = {
                k: args[k]
                for k in ("persona", "instructions", "description")
                if isinstance(args.get(k), str) and args[k].strip()
            }
            confirmed = bool(args.get("confirmed", False))
            if not confirmed and not service._dispatch_approved(None):
                return json.dumps(
                    {
                        "ok": False,
                        "needs_confirmation": True,
                        "error": (
                            "creating a bot needs the user's ok — ask via ask_user, "
                            "then retry with confirmed=true"
                        ),
                    }
                )
            proj = store.create(name=name, **fields)
            return json.dumps(
                {
                    "ok": True,
                    "bot": {
                        "id": proj.id,
                        "handle": proj.handle,
                        "name": proj.name,
                        "vault_path": proj.vault_path,
                    },
                }
            )

        if action == "delete":
            proj, err = _find_bot(store, args.get("bot", ""))
            if err:
                return json.dumps({"ok": False, "error": err})
            chats = args.get("chats", "archive")
            confirmed = bool(args.get("confirmed", False))
            if not confirmed and not service._dispatch_approved(proj.id):
                return json.dumps(
                    {
                        "ok": False,
                        "needs_confirmation": True,
                        "bot": {"id": proj.id, "handle": proj.handle, "name": proj.name},
                        "error": (
                            "deleting a bot needs the user's ok — ask via ask_user, "
                            "then retry with confirmed=true"
                        ),
                    }
                )
            from .server.routes.bots import _delete_bot_impl

            try:
                result = await _delete_bot_impl(service.store, proj.id, chats)
                return json.dumps({"ok": True, **result})
            except Exception as exc:  # noqa: BLE001 — tool result, not a crash
                detail = getattr(exc, "detail", None) or str(exc)
                return json.dumps({"ok": False, "error": f"delete failed: {detail}"})

        if action == "get":
            proj, err = _find_bot(store, args.get("bot", ""))
            if err:
                return json.dumps({"ok": False, "error": err})
            return json.dumps(
                {
                    "ok": True,
                    "bot": {
                        "id": proj.id,
                        "handle": proj.handle,
                        "name": proj.name,
                        "description": proj.description,
                        "persona": proj.persona,
                        "instructions": proj.instructions,
                        "vault_path": proj.vault_path,
                    },
                }
            )

        if action == "update":
            proj, err = _find_bot(store, args.get("bot", ""))
            if err:
                return json.dumps({"ok": False, "error": err})
            fields = {
                k: args[k]
                for k in ("persona", "instructions", "name", "description")
                if isinstance(args.get(k), str) and args[k].strip()
            }
            if not fields:
                return json.dumps(
                    {"ok": False, "error": "nothing to update: pass persona/instructions/name/description"}
                )
            confirmed = bool(args.get("confirmed", False))
            if not confirmed and not service._dispatch_approved(proj.id):
                preview = {k: (v[:120] + "…") if len(v) > 120 else v for k, v in fields.items()}
                return json.dumps(
                    {
                        "ok": False,
                        "needs_confirmation": True,
                        "bot": {"id": proj.id, "handle": proj.handle, "name": proj.name},
                        "fields": preview,
                        "error": (
                            "updating a bot needs the user's ok — ask via ask_user, "
                            "then retry with confirmed=true"
                        ),
                    }
                )
            store.update(proj.id, **fields)
            updated = store.get(proj.id)
            return json.dumps(
                {
                    "ok": True,
                    "bot": {
                        "id": updated.id,
                        "handle": updated.handle,
                        "name": updated.name,
                    },
                    "updated_fields": list(fields),
                }
            )

        return json.dumps({"ok": False, "error": f"unknown action: {action}"})
    except Exception as exc:  # noqa: BLE001 — tool result, not a crash
        return json.dumps({"ok": False, "error": f"bot_config failed: {exc}"})


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
