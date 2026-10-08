"""Telegram bot commands — mirrors chat_slash.py semantics where applicable.

Commands are dispatched by the router after allowlist auth. They operate
directly on the session store / binding store and never spin up the
agent loop (except /compact, which uses the LLM summarizer like the UI's
Compact button, i.e. POST /sessions/{sid}/compact).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from ..server.project_store import ProjectStore
from .api import TelegramClient
from .bindings import TelegramBinding, TelegramBindingStore
from .formatting import split_for_telegram

log = logging.getLogger(__name__)

_HELP_TEXT = """<b>Nexus commands</b>
<code>/project [name]</code> — attach this chat/topic to a project (or show binding)
<code>/topics</code> — list every linked chat/topic
<code>/vault [path]</code> — browse the vault; files arrive as documents
<code>/new [title]</code> — start a new chat here
<code>/chats</code> — list chats in this chat/topic
<code>/switch</code> — switch the active chat (buttons)
<code>/compact [aggressive]</code> — free up context window
<code>/title [new title]</code> — rename the active chat
<code>/usage</code> — token + tool usage for this chat
<code>/cancel</code> — cancel the running turn or dismiss a pending question
<code>/id</code> — chat/thread/user ids (for config)

Plain messages go to the active chat. Topics and groups work standalone —
attach one to a project any time with /project."""


@dataclass
class CommandDeps:
    client: TelegramClient
    agent: Any
    store: Any  # SessionStore
    bindings: TelegramBindingStore
    projects: ProjectStore
    cfg: Any  # TelegramConfig
    router: Any = None  # TelegramRouter — registers /vault menu listings


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
    # message_id of the message this one replies to (0 when not a reply).
    # Binds a free-form HITL answer to the exact prompt bubble it answers
    # (ForceReply sends the quote automatically).
    reply_to_message_id: int = 0


async def _reply(deps: CommandDeps, info: MsgInfo, text: str) -> None:
    """Send text to the originating chat, chunked to fit Telegram limits."""
    for chunk in split_for_telegram(text):
        await deps.client.send_text_safe(info.chat_id, chunk, thread_id=info.thread_id or None)


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
    rows = [[{"text": p.name[:60], "callback_data": f"pj:{p.id[:56]}"}] for p in projects[:20]]
    return {"inline_keyboard": rows}


_VAULT_PAGE = 16


async def cmd_vault(deps: CommandDeps, info: MsgInfo, args: str) -> None:
    """Browse the vault from Telegram: no args → root folders; a folder
    path → its children; a file path → the file as a document."""
    path = args.strip().strip("/")
    await vault_browse(deps, info.chat_id, info.thread_id, -2, 0, path=path)


async def vault_browse(
    deps: CommandDeps,
    chat_id: int,
    thread_id: int,
    idx: int,
    menu_message_id: int,
    *,
    path: str | None = None,
) -> None:
    """Render one vault directory level as buttons (vd: callbacks), or send
    a file. ``idx`` indexes the previous listing's paths (-1 = parent,
    -2 = fresh from cmd_vault with ``path``)."""
    from ..vault import list_tree

    router = deps.router
    if path is None:
        listing = router._vault_menus.get((chat_id, menu_message_id)) if router else None
        if listing is None or not 0 <= idx < len(listing):
            await deps.client.send_text_safe(
                chat_id,
                "Menu expirado — usa /vault de novo.",
                thread_id=thread_id or None,
            )
            return
        path = listing[idx]
    path = (path or "").strip("/")

    entries = await asyncio.to_thread(list_tree)
    by_path = {e.path: e for e in entries}

    # Target is a file → send it.
    if path and by_path.get(path) is not None and by_path[path].type == "file":
        status = (
            await router._send_vault_file(chat_id, thread_id, path) if router else "unavailable"
        )
        await deps.client.send_text_safe(chat_id, status, thread_id=thread_id or None)
        return

    # Children of `path` (or root when empty).
    prefix = f"{path}/" if path else ""
    folders, files = [], []
    for e in entries:
        p = e.path
        if not p.startswith(prefix):
            continue
        rest = p[len(prefix) :]
        if "/" in rest:
            child = rest.split("/", 1)[0]
            if child not in folders:
                folders.append(child)
        else:
            if e.type == "dir":
                if rest not in folders:
                    folders.append(rest)
            else:
                files.append(rest)

    if not path and not folders and not files:
        await deps.client.send_text_safe(chat_id, "Vault vazio.", thread_id=thread_id or None)
        return
    if path and not folders and not files:
        await deps.client.send_text_safe(
            chat_id, f"Pasta vazia: <code>{path}</code>", thread_id=thread_id or None
        )
        return

    paths: list[str] = []
    rows: list[list[dict]] = []
    if path:
        paths.append(path.rsplit("/", 1)[0] if "/" in path else "")
        rows.append([{"text": "⬆︎ ..", "callback_data": "vd:0"}])
    for name in folders[:_VAULT_PAGE]:
        paths.append(f"{prefix}{name}")
        rows.append([{"text": f"📁 {name[:56]}", "callback_data": f"vd:{len(paths) - 1}"}])
    for name in files[:_VAULT_PAGE]:
        paths.append(f"{prefix}{name}")
        rows.append([{"text": f"📄 {name[:56]}", "callback_data": f"vd:{len(paths) - 1}"}])

    label = f"/{path}" if path else "Vault"
    total = len(folders) + len(files)
    shown = min(len(rows) - (1 if path else 0), _VAULT_PAGE)
    msg_id = await deps.client.send_text_safe(
        chat_id,
        f"<b>{label}</b> · {shown}/{total}",
        thread_id=thread_id or None,
        reply_markup={"inline_keyboard": rows} if rows else None,
    )
    if router:
        router._remember_vault_menu(chat_id, msg_id, paths)


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
        project = f"\nproject: <code>{binding.project_id}</code>" + (f" ({p.name})" if p else "")
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
        # Release the active chat too so it stops showing under the project
        # (get_or_create never un-stamps on its own). Best-effort.
        if binding.active_session_id:
            try:
                deps.projects.move_session(binding.active_session_id, None)
            except Exception:
                log.warning(
                    "telegram: failed to un-stamp session %s from project",
                    binding.active_session_id,
                )
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
                "This chat is not bound to a project yet.\nUse /project &lt;name&gt; to bind one."
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
                await _reply(
                    deps, info, text + "\n(No projects exist yet — create one in the Nexus UI.)"
                )
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


_KIND_ICON = {"dm": "✉️", "group": "👥", "topic": "🧵"}


async def cmd_topics(deps: CommandDeps, info: MsgInfo, args: str) -> None:
    """List every Telegram binding (dm/group/topic) with its project and
    active chat — the bot-side mirror of Settings → Telegram's table."""
    bindings = deps.bindings.list_all()
    if not bindings:
        await _reply(
            deps,
            info,
            "No chats or topics are linked yet.\nSend a message anywhere to start "
            "chatting — /project &lt;name&gt; attaches a topic to a project.",
        )
        return
    projects = {p.id: p for p in deps.projects.list(limit=200)}
    lines = ["<b>Linked chats &amp; topics</b>"]
    for b in bindings[:25]:
        icon = _KIND_ICON.get(b.kind, "💬")
        where = f"chat <code>{b.chat_id}</code>"
        if b.thread_id:
            where += f" · topic <code>{b.thread_id}</code>"
        if b.project_id and b.project_id in projects:
            proj = projects[b.project_id].name
        else:
            proj = b.project_id or "no project"
        title = ""
        if b.active_session_id:
            s = deps.store.get(b.active_session_id)
            if s is not None and s.title:
                title = f" · {(s.title or '')[:40]}"
        here = " ✅" if b.chat_id == info.chat_id and b.thread_id == info.thread_id else ""
        lines.append(f"{icon} <b>{proj}</b> · {where}{title}{here}")
    await _reply(deps, info, "\n".join(lines))


async def cmd_new(deps: CommandDeps, info: MsgInfo, args: str) -> None:
    binding = deps.bindings.get(info.chat_id, info.thread_id)
    kind = _binding_kind(info)
    title = args.strip()

    # No binding yet (e.g. /new before any message) — start a standalone
    # chat; /project can attach one later. With a binding, the new chat
    # inherits its project (if any).
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
        f"New chat <b>{shown}</b> started" + (" in project." if project_id else "."),
    )


async def _list_sessions(deps: CommandDeps, binding: TelegramBinding) -> list[Any]:
    if binding.project_id:
        return deps.store.list(limit=25, project_id=binding.project_id)
    # DMs / project-less groups & topics: sessions created from this chat,
    # by context prefix.
    prefix = _context_for(
        MsgInfo(
            binding.chat_id,
            binding.thread_id,
            "private" if binding.kind == "dm" else "supergroup",
            0,
            "",
        )
    )
    rows = deps.store._loom._db.execute(
        "SELECT id, title, updated_at, 0 AS message_count, project_id "
        "FROM sessions WHERE context LIKE ? AND COALESCE(hidden,0)=0 "
        "ORDER BY updated_at DESC LIMIT 25",
        (prefix + "%",),
    ).fetchall()
    return [
        type(
            "S",
            (),
            {
                "id": r[0],
                "title": r[1] or "New session",
                "updated_at": r[2],
                "message_count": r[3],
                "project_id": r[4],
            },
        )()
        for r in rows
    ]


async def cmd_chats(deps: CommandDeps, info: MsgInfo, args: str) -> None:
    binding = deps.bindings.get(info.chat_id, info.thread_id)
    if binding is None:
        await _reply(deps, info, "No chats yet — /new to start one, or just send a message.")
        return

    sessions = await _list_sessions(deps, binding)
    if not sessions:
        await _reply(deps, info, "No chats yet — /new to start one, or just send a message.")
        return

    rows = []
    for i, s in enumerate(sessions):
        marker = "● " if s.id == binding.active_session_id else ""
        rows.append(
            [
                {
                    "text": f"{marker}{(s.title or 'New session')[:56]}",
                    "callback_data": f"sw:{s.id[:56]}",
                }
            ]
        )
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
        await _reply(
            deps, info, f"Current title: <b>{current}</b>\nUsage: /title &lt;new title&gt;"
        )
        return
    deps.store.rename(binding.active_session_id, new_title[:120])
    await _reply(deps, info, f"Chat renamed to <b>{new_title}</b>.")


async def cmd_usage(deps: CommandDeps, info: MsgInfo, args: str) -> None:
    binding = deps.bindings.get(info.chat_id, info.thread_id)
    if binding is None:
        await _reply(deps, info, "No active chat in this conversation yet.")
        return
    row = deps.store._loom._db.execute(
        "SELECT model, input_tokens, output_tokens, tool_call_count FROM sessions WHERE id = ?",
        (binding.active_session_id,),
    ).fetchone()
    if row is None:
        await _reply(deps, info, "Chat not found.")
        return
    model = row[0] or "(no model recorded)"
    in_tok, out_tok, tools = int(row[1] or 0), int(row[2] or 0), int(row[3] or 0)
    text = (
        f"<b>Usage</b> ({model})\n"
        f"input tokens: {in_tok:,}\n"
        f"output tokens: {out_tok:,}\n"
        f"tool calls: {tools:,}"
    )
    # Telegram has no status bar, so /usage is the only place a context
    # reading can surface on this surface.
    try:
        from ..agent.loop.overflow import (
            effective_context_window,
            estimate_tokens,
            usable_tokens,
        )
        from ..agent.loop.zones import classify_zone
        from ..config_file import load_cached

        session = deps.store.get(binding.active_session_id)
        history = (session.history if session else None) or []
        cfg = load_cached()
        configured = 0
        for entry in cfg.models:
            if entry.id == model or entry.model_name == model:
                configured = int(entry.context_window or 0)
                break
        window = effective_context_window(model, configured)
        usable = usable_tokens(window)
        est = estimate_tokens(history) if history else 0
        pct = round(est / usable * 100, 1) if usable else 0.0
        text += (
            f"\ncontext: {est:,} / {usable:,} usable ({pct}%)"
            f"\nzone: {classify_zone(est, window)}"
        )
    except Exception:  # noqa: BLE001 — /usage must still answer
        log.debug("telegram /usage: context reading failed", exc_info=True)
    await _reply(deps, info, text)


async def cmd_cancel(deps: CommandDeps, info: MsgInfo, args: str) -> None:
    from ..server.services.chat_turn_runner import cancel_running_turn

    # A pending free-form answer owns this chat first — /cancel dismisses
    # the question (and the turn waiting on it) instead of the turn alone.
    router = deps.router
    if router is not None:
        slot = await router.dismiss_pending_answer(info)
        if slot is not None:
            cancel_running_turn(slot.session_id)
            router.cancel_resume(slot.session_id)
            await _reply(deps, info, "Dismissed the pending question.")
            return

    binding = deps.bindings.get(info.chat_id, info.thread_id)
    if binding is None:
        await _reply(deps, info, "No active chat in this conversation yet.")
        return
    if cancel_running_turn(binding.active_session_id):
        await _reply(deps, info, "Cancelling the running turn…")
    elif router is not None and router.cancel_resume(binding.active_session_id):
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

    await deps.client.send_chat_action(info.chat_id, "typing", thread_id=info.thread_id or None)
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
    "topics": cmd_topics,
    "vault": cmd_vault,
    "new": cmd_new,
    "chats": cmd_chats,
    "switch": cmd_switch,
    "title": cmd_title,
    "usage": cmd_usage,
    "cancel": cmd_cancel,
    "compact": cmd_compact,
}
