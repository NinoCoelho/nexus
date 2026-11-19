"""MCP-facing chat tool: external agents converse with Nexus bots.

Exposes two ad-hoc tools on the Nexus MCP server (outside the live tool
registry so they don't leak into the agent's own schema):

``nexus_chat`` {message, bot?, session_id?, new_session?, title?, wait?,
timeout_s?}
    Send a message to a bot (resolved by @handle / project id) or to an
    existing session, wait for the settled reply, and return it.
    With ``wait:false`` the call registers a background job and returns a
    job id immediately; poll ``nexus_chat_status`` for the verdict.

``nexus_chat_status`` {job_id}
    Running/done/failed/waiting_user + final reply or error for an
    asynchronous ``nexus_chat`` job.

A turn that parks on a HITL question (ask_user) returns
``{status: "waiting_user", ...}`` instead of blocking — the owner answers
through the web UI or Telegram, and the next call delivers the settled
reply.

Resolved targets:
- ``session_id``  → that exact session (must exist)
- ``bot``         → @handle or project id; creates a fresh session when
                    ``new_session:true`` or the bot has no session yet,
                    otherwise reuses its most recent non-archived session
- omitted         → error ("bot or session_id required")
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from nexus.home import sessions_db
from nexus.server.project_store import ProjectStore

log = logging.getLogger(__name__)

# Bounded in-memory window for async job results (mirrors agent_delegate's
# _RESULTS pattern) — finished verdicts live here so callers never need to
# re-read a log file.
_CHAT_JOBS: dict[str, dict[str, Any]] = {}
_MAX_JOBS = 50


class _McpToolResult:
    """Small stand-in for loom's tool result — the bridge reads ``.text``."""

    def __init__(self, text: str) -> None:
        self.text = text


class _McpToolHandler:
    """Minimal MCP tool handler for tools that live outside the registry.

    Mirrors the surface loom's registry handlers expose
    (``.tool.name/.description/.parameters`` + ``await .invoke(args)``) so
    the bridge consumes them identically to registered tools.
    """

    def __init__(self, name: str, description: str, parameters: dict[str, Any], fn: Any) -> None:
        self.tool = _McpTool(name, description, parameters)
        self.fn = fn

    async def invoke(self, arguments: dict[str, Any]) -> _McpToolResult:
        try:
            result = await self.fn(arguments or {})
        except Exception as exc:  # never let a raw traceback kill the MCP stream
            log.exception("[mcp-chat] tool %s failed", self.tool.name)
            return _McpToolResult(f"error: {type(exc).__name__}: {exc}")
        text = result if isinstance(result, str) else str(result)
        return _McpToolResult(text)


class _McpTool:
    def __init__(self, name: str, description: str, parameters: dict[str, Any]) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters


# ---------------------------------------------------------------------------
# session + target resolution
# ---------------------------------------------------------------------------


def _known_handles(project_store: ProjectStore) -> list[str]:
    try:
        return [p.handle for p in project_store.list(limit=200)]
    except Exception:  # noqa: BLE001 — best-effort handle listing for error hints
        return []


def _pick_session(
    *,
    bot: str | None,
    session_id: str | None,
    new_session: bool,
    store: Any,
    project_store: ProjectStore,
) -> tuple[Any, str | None, str | None]:
    """Pick (or create) the session to run the turn on.

    Returns ``(session, project_id, error)``.
    """
    if session_id:
        session = store.get(session_id)
        if session is None:
            return None, None, f"unknown session {session_id}"
        try:
            project = project_store.get_project_for_session(session_id)
        except Exception:  # noqa: BLE001
            project = None
        return session, getattr(project, "id", None), None

    if not bot:
        return None, None, "bot or session_id required"

    project = project_store.get_by_handle(bot)
    if project is None:
        project = project_store.get(bot)
    if project is None:
        return None, None, (
            f"unknown bot {bot!r} — known handles: {', '.join(_known_handles(project_store)) or '(none)'}"
        )

    project_id = project.id

    if new_session:
        session = store.create(project_id=project_id)
        log.info("[mcp-chat] created new session %s for bot %s (@%s)", session.id, project.name, project.handle)
        return session, project_id, None

    # reuse most recent non-archived session of the project
    rows = store.list(project_id=project_id, limit=1, archived=False, include_hidden=False)
    if rows:
        session = store.get(rows[0].id)
        if session is not None:
            return session, project_id, None

    session = store.create(project_id=project_id)
    log.info("[mcp-chat] created new session %s for bot %s (@%s)", session.id, project.name, project.handle)
    return session, project_id, None


# ---------------------------------------------------------------------------
# turn execution
# ---------------------------------------------------------------------------


async def _run_turn(
    *,
    agent: Any,
    store: Any,
    tracker: Any,
    session: Any,
    message: str,
    wait: bool,
    timeout_s: float,
) -> dict[str, Any]:
    """Launch + (optionally) wait for one turn. Mirrors coordinator.dispatch."""

    from nexus.server.services.turn_launcher import launch_turn

    outcome = await launch_turn(
        agent=agent,
        store=store,
        tracker=tracker,
        session=session,
        message=message,
        origin="mcp",
    )
    if outcome.error:
        return {"ok": False, "error": outcome.error, "session_id": session.id}
    if outcome.queued:
        return {"ok": True, "queued": True, "session_id": session.id}
    if not wait or outcome.runner is None:
        return {"ok": True, "dispatched": True, "session_id": session.id}

    try:
        await asyncio.wait_for(outcome.runner.task, timeout=timeout_s)
    except TimeoutError:
        return {"ok": False, "error": "turn timed out", "session_id": session.id, "status": "timeout"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"turn failed: {exc}", "session_id": session.id}

    refreshed = store.get(session.id)
    reply = ""
    truncated = False
    if refreshed is not None:
        for m in reversed(list(getattr(refreshed, "history", []))):
            role = getattr(getattr(m, "role", None), "value", "")
            content = (getattr(m, "content", "") or "").strip()
            if role == "assistant" and content:
                reply = content
                break
    if len(reply) > 4000:
        truncated = True
        reply = reply[:4000]
    return {"ok": True, "session_id": session.id, "reply": reply, "truncated": truncated}


# ---------------------------------------------------------------------------
# the two ad-hoc MCP tools
# ---------------------------------------------------------------------------


async def _nexus_chat(args: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
    message = (args or {}).get("message") or ""
    if not message.strip():
        return {"ok": False, "error": "message is required"}

    bot = (args or {}).get("bot")
    session_id = (args or {}).get("session_id")
    new_session = bool((args or {}).get("new_session", False))
    wait = bool((args or {}).get("wait", True))
    timeout_s = float((args or {}).get("timeout_s", 120))

    store = deps["store"]
    tracker = deps["tracker"]
    agent = deps["agent"]
    project_store = deps.get("project_store") or ProjectStore(sessions_db())
    main_loop = deps.get("main_loop")

    if main_loop is None:
        log.warning(
            "[mcp-chat] main_loop not in chat_deps — falling back to thread-local loop "
            "(turn will not survive MCP request disconnect); wire main_loop in app_lifespan"
        )

    session, _project_id, err = _pick_session(
        bot=bot,
        session_id=session_id,
        new_session=new_session,
        store=store,
        project_store=project_store,
    )
    if err:
        return {"ok": False, "error": err}

    # --- synchronous ---------------------------------------------------------
    if wait:
        coro = _run_turn(
            agent=agent,
            store=store,
            tracker=tracker,
            session=session,
            message=message,
            wait=True,
            timeout_s=timeout_s,
        )
        if main_loop is not None:
            # Run the turn on the *main* event loop (where the Nexus server
            # lives) so it survives disconnect of this MCP request thread.
            future = asyncio.run_coroutine_threadsafe(coro, main_loop)
            return await asyncio.wait_for(asyncio.wrap_future(future), timeout=timeout_s)
        return await coro

    # --- asynchronous: register job, run in background ----------------------
    job_id = uuid.uuid4().hex[:12]
    _CHAT_JOBS[job_id] = {
        "job_id": job_id,
        "status": "running",
        "session_id": session.id,
        "reply": None,
        "error": None,
        "queued": False,
    }
    while len(_CHAT_JOBS) > _MAX_JOBS:
        _CHAT_JOBS.pop(next(iter(_CHAT_JOBS)))

    async def _run() -> None:
        try:
            result = await _run_turn(
                agent=agent,
                store=store,
                tracker=tracker,
                session=session,
                message=message,
                wait=True,
                timeout_s=timeout_s,
            )
            entry = _CHAT_JOBS[job_id]
            entry["status"] = "done" if result.get("ok") else "failed"
            entry["reply"] = result.get("reply")
            entry["error"] = result.get("error")
            entry["queued"] = result.get("queued", False)
        except Exception as exc:  # never lose the verdict
            log.exception("[mcp-chat] job %s crashed", job_id)
            entry = _CHAT_JOBS.get(job_id)
            if entry is not None:
                entry["status"] = "failed"
                entry["error"] = str(exc)

    if main_loop is not None:
        # Run the background turn on the *main* event loop so it survives
        # disconnect of this MCP request thread and of the MCP bridge itself.
        asyncio.run_coroutine_threadsafe(_run(), main_loop)
    else:
        asyncio.create_task(_run())
    return {"ok": True, "job_id": job_id, "status": "running", "session_id": session.id}


async def _nexus_chat_status(args: dict[str, Any], deps: dict[str, Any]) -> dict[str, Any]:
    job_id = (args or {}).get("job_id")
    if not job_id:
        return {"ok": False, "error": "job_id is required"}
    entry = _CHAT_JOBS.get(job_id)
    if entry is None:
        return {"ok": False, "error": f"unknown job {job_id}"}
    return dict(entry)


NEXUS_CHAT_TOOLS = [
    _McpToolHandler(
        name="nexus_chat",
        description=(
            "Send a message to a Nexus bot and wait for its settled reply. "
            "Target a bot by @handle or project id (a fresh session is created "
            "when none exists or new_session=true), or address an existing "
            "session_id directly. Set wait:false to get a job id and poll "
            "nexus_chat_status instead of blocking."
        ),
        parameters={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "The user message to send to the bot."},
                "bot": {
                    "type": "string",
                    "description": "Bot @handle or project id to talk to. Mutually exclusive with session_id.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Existing session id to continue. Mutually exclusive with bot.",
                },
                "new_session": {
                    "type": "boolean",
                    "description": "Always create a fresh session for the bot (default false).",
                },
                "wait": {
                    "type": "boolean",
                    "description": "Wait for the settled reply (default true). false returns a job id.",
                },
                "timeout_s": {
                    "type": "number",
                    "description": "Seconds to wait for the reply when wait=true (default 120).",
                },
            },
            "required": ["message"],
            "additionalProperties": False,
        },
        fn=lambda args: _nexus_chat(args, _DEPS),
    ),
    _McpToolHandler(
        name="nexus_chat_status",
        description=(
            "Poll the status of an asynchronous nexus_chat job (wait:false). "
            "Returns running/done/failed/waiting_user plus the final reply or error."
        ),
        parameters={
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Job id returned by nexus_chat with wait:false.",
                }
            },
            "required": ["job_id"],
            "additionalProperties": False,
        },
        fn=lambda args: _nexus_chat_status(args, _DEPS),
    ),
]

# Late-bound deps, wired by start_mcp_server (app_lifespan passes them in).
_DEPS: dict[str, Any] = {}


def wire_chat_deps(deps: dict[str, Any]) -> None:
    """Inject the runtime deps (agent, store, tracker, project_store) so the
    ad-hoc chat tools can launch turns. Called once from start_mcp_server."""
    _DEPS.clear()
    _DEPS.update(deps)
