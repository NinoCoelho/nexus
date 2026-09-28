"""Telegram bot commands — mirrors chat_slash.py semantics where applicable.

Commands are dispatched by the router after allowlist auth. They operate
directly on the session store / binding store and never spin up the
agent loop (except /compact, which uses the LLM summarizer like the UI's
Compact button, i.e. POST /sessions/{sid}/compact).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..server.project_store import ProjectStore
from .api import TelegramClient
from .bindings import TelegramBinding, TelegramBindingStore
from .formatting import split_for_telegram

log = logging.getLogger(__name__)

_HELP_TEXT = """<b>Nexus commands</b>
<code>/project [name]</code> — bind this chat/topic to a project (or show binding)
<code>/new [title]</code> — start a new chat in this project
<code>/chats</code> — list chats for this project
<code>/switch</code> — switch the active chat (buttons)
<code>/compact [aggressive]</code> — free up context window
<code>/title [new title]</code> — rename the active chat
<code>/usage</code> — token + tool usage for this chat
<code>/cancel</code> — cancel the running turn
<code>/id</code> — chat/thread/user ids (for config)

Plain messages go to the project's active chat. In forum groups, each
topic can be bound to its own project via /project."""


@dataclass
class CommandDeps:
    client: TelegramClient
    agent: Any
    store: Any  # SessionStore
    bindings: TelegramBindingStore
    projects: ProjectStore
    cfg: Any  # TelegramConfig


@dataclass
class MsgInfo:
    chat_id: int
    thread_id: int
    chat_type: str  # 'private' | 'group' | 'supergroup'
    user_id: int
    user_label: str  # "Alice" / "Alice (@alice)"
    # Telegram message_id of the incoming update — used by the router to
    # place the reaction ack (👀 → 👍). 0 for synthetic contexts.
    message_id: int = 0


async def _reply(deps: CommandDeps, info: MsgInfo, text: str) -> None:
    """Send text to the originating chat, chunked to fit Telegram limits."""
    for chunk in split_for_telegram(text):
        await deps.client.send_text_safe(
            info.chat_id, chunk, thread_id=info.thread_id or None
        )


def _is_forum_topic(info: MsgInfo) -> bool:
    return info.chat_type in ("group", "supergroup") and info.thread_id != 0


def _binding_kind(info: MsgInfo) -> str:
    if info.chat_type == "private":
        return "dm"
    return "topic" if _is_forum_topic(info) else "group"


def _context_for(info: MsgInfo) -> str:
    kind = _binding_kind(info)
    if kind == "dm":
        return f"Telegram: dm {info.chat_id}"
    if kind == "topic":
        return f"Telegram: topic {info.chat_id}/{info.thread_id}"
    return f"Telegram: group {info.chat_id}"


async def _find_project(deps: CommandDeps, name_or_id: str) -> Any:
    pstore = deps.projects
    name = name_or_id.strip()
    if not name:
        return None
    projects = pstore.list(limit=200)
    for p in projects:
        if p.id == name or p.name.lower() == name.lower():
            return pstore.get(p.id)
    # substring match as fallback
    for p in projects:
        if name.lower() in p.name.lower():
            return pstore.get(p.id)
    return None


def _project_keyboard(projects: list[Any]) -> dict | None:
    """Inline keyboard listing projects for /project (no-arg) binding."""
    if not projects:
        return None
    rows = [
        [{"text": p.name[:60], "callback_data": f"pj:{p.id[:56]}"}]
        for p in projects[:20]
    ]
    return {"inline_keyboard": rows}


# ─────────────────────────────────────────────────────────────────────────────
# Handlers
# ─────────────────────────────────────────────────────────────────────────────


async def cmd_help(deps: CommandDeps, info: MsgInfo, args: str) -> None:
    await _reply(deps, info, _HELP_TEXT)


async def cmd_id(deps: CommandDeps, info: MsgInfo, args: str) -> None:
    binding = deps.bindings.get(info.chat_id, info.thread_id)
    project = ""
    if binding and binding.project_id:
        p = deps.projects.get(binding.project_id)
        project = f"\nproject: <code>{binding.project_id}</code>" + (
            f" ({p.name})" if p else ""
        )
    await _reply(
        deps,
        info,
        f"chat id: <code>{info.chat_id}</code>\n"
        f"thread id: <code>{info.thread_id}</code>\n"
        f"user id: <code>{info.user_id}</code>"
        f"{project}",
    )


async def cmd_project(deps: CommandDeps, info: MsgInfo, args: str) -> None:
    pstore = deps.projects
    kind = _binding_kind(info)
    binding = deps.bindings.get(info.chat_id, info.thread_id)

    if args.strip().lower() in ("off", "none", "unbind"):
        if binding is None:
            await _reply(deps, info, "This chat is not bound to a project.")
            return
        deps.bindings.set_project(info.chat_id, info.thread_id, None)
        await _reply(deps, info, "Unbound. /project &lt;name&gt; to bind again.")
        return

    if not args.strip():
        # Show current binding + available projects as buttons.
        if binding and binding.project_id:
            p = pstore.get(binding.project_id)
            name = p.name if p else binding.project_id
            await _reply(deps, info, f"This chat is bound to project <b>{name}</b>.")
        else:
            text = (
                "This chat is not bound to a project yet.\n"
                "Use /project &lt;name&gt; to bind one."
            )
            projects = pstore.list(limit=20)
            kb = _project_keyboard(projects)
            if kb:
                await deps.client.send_text_safe(
                    info.chat_id,
                    text + "\n\nTap a project to bind:",
                    thread_id=info.thread_id or None,
                    reply_markup=kb,
                )
            else:
                await _reply(deps, info, text + "\n(No projects exist yet — create one in the Nexus UI.)")
        return

    project = await _find_project(deps, args)
    if project is None:
        await _reply(deps, info, f"No project matching <b>{args.strip()}</b>.")
        return

    # Adopt the project's most recent chat as the "main chat" (continue where
    # the project left off), or create a fresh one for a new project.
    existing = deps.store.list(limit=1, project_id=project.id)
    if existing:
        session_id = existing[0].id
        title = existing[0].title
    else:
        session = deps.store.create(context=_context_for(info), project_id=project.id)
        session_id = session.id
        title = session.title

    if binding is None:
        deps.bindings.upsert(
            chat_id=info.chat_id,
            thread_id=info.thread_id,
            kind=kind,
            project_id=project.id,
            active_session_id=session_id,
        )
    else:
        deps.bindings.set_project(info.chat_id, info.thread_id, project.id)
        deps.bindings.set_active_session(info.chat_id, info.thread_id, session_id)

    await _reply(
        deps,
        info,
        f"Bound to project <b>{project.name}</b>.\n"
        f"Active chat: <b>{title}</b> — messages here go to it.\n"
        f"Use /new for another chat, /chats to list them.",
    )


async def cmd_new(deps: CommandDeps, info: MsgInfo, args: str) -> None:
    binding = deps.bindings.get(info.chat_id, info.thread_id)
    kind = _binding_kind(info)
    title = args.strip()

    if kind != "dm" and binding is None:
        await _reply(deps, info, "Bind this chat to a project first: /project &lt;name&gt;")
        return

    project_id = binding.project_id if binding else None
    session = deps.store.create(context=_context_for(info), project_id=project_id)
    if title:
        deps.store.rename(session.id, title[:120])
    if binding is None:
        deps.bindings.upsert(
            chat_id=info.chat_id,
            thread_id=info.thread_id,
            kind=kind,
            project_id=project_id,
            active_session_id=session.id,
        )
    else:
        deps.bindings.set_active_session(info.chat_id, info.thread_id, session.id)

    shown = title or session.title
    await _reply(
        deps,
        info,
        f"New chat <b>{shown}</b> started"
        + (" in project." if project_id else "."),
    )


async def _list_sessions(deps: CommandDeps, binding: TelegramBinding) -> list[Any]:
    if binding.project_id:
        return deps.store.list(limit=25, project_id=binding.project_id)
    # DMs / unbound groups: sessions created from this chat, by context prefix.
    prefix = _context_for(
        MsgInfo(binding.chat_id, binding.thread_id, "private" if binding.kind == "dm" else "supergroup", 0, "")
    )
    rows = deps.store._loom._db.execute(
        "SELECT id, title, updated_at, 0 AS message_count, project_id "
        "FROM sessions WHERE context LIKE ? AND COALESCE(hidden,0)=0 "
        "ORDER BY updated_at DESC LIMIT 25",
        (prefix + "%",),
    ).fetchall()
    return [
        type("S", (), {
            "id": r[0], "title": r[1] or "New session",
            "updated_at": r[2], "message_count": r[3], "project_id": r[4],
        })()
        for r in rows
    ]


async def cmd_chats(deps: CommandDeps, info: MsgInfo, args: str) -> None:
    binding = deps.bindings.get(info.chat_id, info.thread_id)
    if binding is None:
        await _reply(deps, info, "No chats yet — /project &lt;name&gt; to bind one, or just send a message.")
        return

    sessions = await _list_sessions(deps, binding)
    if not sessions:
        await _reply(deps, info, "No chats found for this project yet.")
        return

    rows = []
    for i, s in enumerate(sessions):
        marker = "● " if s.id == binding.active_session_id else ""
        rows.append([{"text": f"{marker}{(s.title or 'New session')[:56]}", "callback_data": f"sw:{s.id[:56]}"}])
    kb = {"inline_keyboard": rows}
    await deps.client.send_text_safe(
        info.chat_id,
        "Chats — tap to switch (● active):",
        thread_id=info.thread_id or None,
        reply_markup=kb,
    )


async def cmd_switch(deps: CommandDeps, info: MsgInfo, args: str) -> None:
    await cmd_chats(deps, info, args)


async def cmd_title(deps: CommandDeps, info: MsgInfo, args: str) -> None:
    binding = deps.bindings.get(info.chat_id, info.thread_id)
    if binding is None:
        await _reply(deps, info, "No active chat in this conversation yet.")
        return
    session = deps.store.get(binding.active_session_id)
    current = session.title if session else "(unknown)"
    new_title = args.strip()
    if not new_title:
        await _reply(deps, info, f"Current title: <b>{current}</b>\nUsage: /title &lt;new title&gt;")
        return
    deps.store.rename(binding.active_session_id, new_title[:120])
    await _reply(deps, info, f"Chat renamed to <b>{new_title}</b>.")


async def cmd_usage(deps: CommandDeps, info: MsgInfo, args: str) -> None:
    binding = deps.bindings.get(info.chat_id, info.thread_id)
    if binding is None:
        await _reply(deps, info, "No active chat in this conversation yet.")
        return
    row = deps.store._loom._db.execute(
        "SELECT model, input_tokens, output_tokens, tool_call_count "
        "FROM sessions WHERE id = ?",
        (binding.active_session_id,),
    ).fetchone()
    if row is None:
        await _reply(deps, info, "Chat not found.")
        return
    model = row[0] or "(no model recorded)"
    in_tok, out_tok, tools = int(row[1] or 0), int(row[2] or 0), int(row[3] or 0)
    await _reply(
        deps,
        info,
        f"<b>Usage</b> ({model})\n"
        f"input tokens: {in_tok:,}\n"
        f"output tokens: {out_tok:,}\n"
        f"tool calls: {tools:,}",
    )


async def cmd_cancel(deps: CommandDeps, info: MsgInfo, args: str) -> None:
    from ..server.services.chat_turn_runner import cancel_running_turn

    binding = deps.bindings.get(info.chat_id, info.thread_id)
    if binding is None:
        await _reply(deps, info, "No active chat in this conversation yet.")
        return
    if cancel_running_turn(binding.active_session_id):
        await _reply(deps, info, "Cancelling the running turn…")
    else:
        await _reply(deps, info, "No turn is running on the active chat.")


async def cmd_compact(deps: CommandDeps, info: MsgInfo, args: str) -> None:
    from ..agent.loop.compact import compact_and_summarize

    binding = deps.bindings.get(info.chat_id, info.thread_id)
    if binding is None:
        await _reply(deps, info, "No active chat in this conversation yet.")
        return
    session_id = binding.active_session_id
    session = deps.store.get(session_id)
    if session is None:
        await _reply(deps, info, "Chat not found.")
        return

    strategy = "aggressive" if "aggressive" in args.lower() else "auto"

    from ..config_file import load_cached as load_config

    cfg = load_config()
    model_id = getattr(cfg.agent, "default_model", "") or None
    provider, upstream_model = deps.agent._resolve_provider(model_id)
    context_window = deps.agent._context_window_for(upstream_model or model_id)

    await deps.client.send_chat_action(
        info.chat_id, "typing", thread_id=info.thread_id or None
    )
    new_history, report = await compact_and_summarize(
        session.history,
        context_window=context_window,
        session_id=session_id,
        model_id=upstream_model or model_id,
        provider=provider,
        strategy=strategy,
    )

    if report.compact_report.compacted > 0 or report.summarized:
        deps.store.replace_history(session_id, new_history)

    await _reply(
        deps,
        info,
        f"<b>Compacted</b> ({strategy})\n"
        f"messages: {report.messages_before} → {report.messages_after}\n"
        f"tokens: ~{report.tokens_before:,} → ~{report.tokens_after:,}\n"
        f"tool results compacted: {report.compact_report.compacted}\n"
        f"summarized: {'yes' if report.summarized else 'no'}\n"
        f"context zone: {report.zone_after}",
    )


# name → handler registry (single source of truth for the router)
COMMANDS: dict[str, Any] = {
    "help": cmd_help,
    "start": cmd_help,
    "id": cmd_id,
    "project": cmd_project,
    "new": cmd_new,
    "chats": cmd_chats,
    "switch": cmd_switch,
    "title": cmd_title,
    "usage": cmd_usage,
    "cancel": cmd_cancel,
    "compact": cmd_compact,
}
