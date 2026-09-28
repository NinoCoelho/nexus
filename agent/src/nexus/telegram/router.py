"""Update routing — auth, commands, chat turns, streamed replies.

Incoming Telegram updates flow through here:

    update → allowlist auth → command? → commands.py
                          → chat message → resolve binding → launch_turn()
                          → session bus → streamed reply (throttled edits)

Turn execution goes through ``services/turn_launcher`` so a Telegram turn
behaves exactly like a UI turn: queue-then-inject while busy, chained
follow-up turns, mid-turn compaction, HITL via the bus. The streamer
below subscribes to the session bus (with replay, mirroring the SSE
route) and progressively edits one Telegram message per turn; queued
chained turns each get their own message (``user_injected`` resets it).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from uuid import uuid4

from ..server.services.turn_launcher import launch_turn
from .api import TelegramClient
from .bindings import TelegramBindingStore
from .commands import COMMANDS, CommandDeps, MsgInfo
from .formatting import md_to_telegram_html, split_for_telegram

log = logging.getLogger(__name__)

_EDIT_INTERVAL = 2.5  # min seconds between progressive message edits
_TYPING_INTERVAL = 4.5
_DENY_THROTTLE = 60.0  # seconds between "not authorized" replies per user
_COMMAND_RE = re.compile(r"^/([a-zA-Z_]+)(@\w+)?\s*(.*)$", re.S)


class TelegramRouter:
    def __init__(
        self,
        *,
        client: TelegramClient,
        agent,
        store,
        tracker,
        bindings: TelegramBindingStore,
        cfg,
        publish_job_event=None,
    ) -> None:
        self.client = client
        self.agent = agent
        self.store = store
        self.tracker = tracker
        self.bindings = bindings
        self.cfg = cfg
        self.publish_job_event = publish_job_event
        from ..server.project_store import ProjectStore

        self.deps = CommandDeps(
            client=client,
            agent=agent,
            store=store,
            bindings=bindings,
            projects=ProjectStore(store._db_path),
            cfg=cfg,
        )
        # session_id → live reply-streamer task
        self._streamers: dict[str, asyncio.Task] = {}
        # (chat_id, thread_id) already shown the unbound-topic hint
        self._hinted: set[tuple[int, int]] = set()
        # user_id → last deny-reply timestamp
        self._deny_last: dict[int, float] = {}
        # short key → (session_id, request_id, answer) for HITL buttons
        self._hitl_buttons: dict[str, tuple[str, str, str]] = {}
        # request_id → (chat_id, thread_id, message_id) for forwarded prompts
        self._hitl_messages: dict[str, tuple[int, int, int]] = {}

    # ── Entry point ──────────────────────────────────────────────────────

    async def handle_update(self, update: dict) -> None:
        try:
            if "callback_query" in update:
                await self._on_callback(update["callback_query"])
            elif "message" in update:
                await self._on_message(update["message"])
        except Exception:
            log.exception("telegram: update handling failed")

    # ── Messages ─────────────────────────────────────────────────────────

    async def _on_message(self, msg: dict) -> None:
        text = (msg.get("text") or msg.get("caption") or "").strip()
        if not text:
            return  # service messages (topic created, joins, photos, …)

        chat = msg.get("chat", {})
        from_user = msg.get("from") or {}
        chat_id = int(chat.get("id", 0))
        chat_type = str(chat.get("type", ""))
        thread_id = int(msg.get("message_thread_id") or 0)
        user_id = int(from_user.get("id") or 0)
        first = str(from_user.get("first_name") or from_user.get("title") or "")
        username = str(from_user.get("username") or "")
        label = f"{first} (@{username})" if username else (first or f"user {user_id}")

        info = MsgInfo(
            chat_id=chat_id,
            thread_id=thread_id,
            chat_type=chat_type,
            user_id=user_id,
            user_label=label,
        )

        if user_id not in (self.cfg.allowed_user_ids or []):
            await self._deny(user_id, chat_id, thread_id)
            return

        if text.startswith("/"):
            await self._dispatch_command(text, info)
        else:
            await self._handle_chat_message(text, chat_type, info)

    async def _deny(self, user_id: int, chat_id: int, thread_id: int) -> None:
        if not self.cfg.deny_message:
            return
        now = time.monotonic()
        if now - self._deny_last.get(user_id, 0.0) < _DENY_THROTTLE:
            return
        self._deny_last[user_id] = now
        try:
            await self.client.send_text_safe(
                chat_id,
                "⛔ You are not authorized to use this bot.",
                thread_id=thread_id or None,
            )
        except Exception:
            log.debug("telegram: deny message failed", exc_info=True)

    async def _dispatch_command(self, text: str, info: MsgInfo) -> None:
        m = _COMMAND_RE.match(text)
        if m is None:
            return
        name, _bot_suffix, args = m.group(1).lower(), m.group(2), m.group(3) or ""
        handler = COMMANDS.get(name)
        if handler is None:
            await self.client.send_text_safe(
                info.chat_id,
                f"Unknown command /{name}. Try /help",
                thread_id=info.thread_id or None,
            )
            return
        try:
            await handler(self.deps, info, args.strip())
        except Exception:
            log.exception("telegram: command /%s failed", name)
            await self.client.send_text_safe(
                info.chat_id,
                f"⚠️ /{name} failed — check the server logs.",
                thread_id=info.thread_id or None,
            )

    # ── Chat messages ────────────────────────────────────────────────────

    async def _handle_chat_message(
        self, text: str, chat_type: str, info: MsgInfo
    ) -> None:
        binding = self.bindings.get(info.chat_id, info.thread_id)

        if binding is None:
            if chat_type == "private":
                binding = await self._create_binding(info, project_id=None)
            elif info.thread_id:  # forum topic — require explicit binding
                if (info.chat_id, info.thread_id) not in self._hinted:
                    self._hinted.add((info.chat_id, info.thread_id))
                    await self.client.send_text_safe(
                        info.chat_id,
                        "This topic isn't linked to a Nexus project yet.\n"
                        "Use /project <name> to bind it, or /help for all commands.",
                        thread_id=info.thread_id,
                    )
                return
            else:  # plain group — auto-create an unprojected chat
                binding = await self._create_binding(info, project_id=None)
                await self.client.send_text_safe(
                    info.chat_id,
                    "This group isn't bound to a project — messages go to a "
                    "standalone chat. Use /project <name> to bind one.",
                )

        session = self.store.get_or_create(
            binding.active_session_id,
            context=self._session_context(info),
            project_id=binding.project_id,
        )

        # Group messages carry the sender so the agent knows who's talking.
        message = text
        if chat_type != "private":
            message = f"From {info.user_label}:\n\n{text}"

        outcome = await launch_turn(
            agent=self.agent,
            store=self.store,
            tracker=self.tracker,
            session=session,
            message=message,
            publish_job_event=self.publish_job_event,
        )

        if outcome.error is not None:
            await self.client.send_text_safe(
                info.chat_id,
                f"⚠️ {outcome.error}",
                thread_id=info.thread_id or None,
            )
            return

        if outcome.queued:
            await self.client.send_text_safe(
                info.chat_id,
                "📥 Queued — I'll answer when the current turn finishes.",
                thread_id=info.thread_id or None,
            )
            # A streamer should already be alive for this session; if it
            # died (idle exit / crash), spawn one so the answer still lands.
            self._ensure_streamer(session.id, info.chat_id, info.thread_id)
            return

        self._ensure_streamer(session.id, info.chat_id, info.thread_id)

    def _session_context(self, info: MsgInfo) -> str:
        if info.chat_type == "private":
            return f"Telegram: dm {info.chat_id}"
        if info.thread_id:
            return f"Telegram: topic {info.chat_id}/{info.thread_id}"
        return f"Telegram: group {info.chat_id}"

    async def _create_binding(self, info: MsgInfo, *, project_id: str | None):
        kind = "dm" if info.chat_type == "private" else (
            "topic" if info.thread_id else "group"
        )
        session = self.store.create(
            context=self._session_context(info), project_id=project_id
        )
        return self.bindings.upsert(
            chat_id=info.chat_id,
            thread_id=info.thread_id,
            kind=kind,
            project_id=project_id,
            active_session_id=session.id,
        )

    # ── Reply streaming ──────────────────────────────────────────────────
    #
    # One long-lived streamer task per session subscribes to the session
    # bus and renders every turn. It never exits on its own — exiting and
    # respawning per turn would race a back-to-back launch into an
    # unstreamed turn (and cancelling a bus subscription mid-``__anext__``
    # kills the generator). ``turn_settled`` just finalizes the current
    # message and resets for the next turn. The task is cancelled only in
    # ``aclose()`` (poller shutdown).

    def _ensure_streamer(
        self, session_id: str, chat_id: int, thread_id: int
    ) -> None:
        existing = self._streamers.get(session_id)
        if existing is not None and not existing.done():
            return
        self._streamers[session_id] = asyncio.create_task(
            self._stream_reply(session_id, chat_id, thread_id),
            name=f"telegram-reply-{session_id[:8]}",
        )

    async def aclose(self) -> None:
        """Cancel all reply streamers (poller shutdown)."""
        for t in list(self._streamers.values()):
            if not t.done():
                t.cancel()
        for t in list(self._streamers.values()):
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._streamers.clear()

    async def _stream_reply(
        self, session_id: str, chat_id: int, thread_id: int
    ) -> None:
        acc = ""
        msg_id = 0
        last_edit = 0.0
        typing_stop = asyncio.Event()
        typing_task = asyncio.create_task(
            self._typing_loop(session_id, chat_id, thread_id, typing_stop)
        )
        try:
            async for sevent in self.store.subscribe_with_replay(session_id):
                ev = sevent.data if hasattr(sevent, "data") else sevent
                etype = ev.get("type")

                if etype == "delta":
                    acc += ev.get("text", "")
                    now = time.monotonic()
                    if msg_id == 0 and acc.strip():
                        msg_id = await self.client.send_text_safe(
                            chat_id,
                            md_to_telegram_html(acc),
                            thread_id=thread_id or None,
                        )
                        last_edit = now
                    elif (
                        msg_id
                        and self.cfg.stream_edits
                        and now - last_edit >= _EDIT_INTERVAL
                    ):
                        ok = await self.client.edit_text_safe(
                            chat_id, msg_id, md_to_telegram_html(acc)
                        )
                        if not ok:  # original message was deleted
                            msg_id = await self.client.send_text_safe(
                                chat_id,
                                md_to_telegram_html(acc),
                                thread_id=thread_id or None,
                            )
                        last_edit = now

                elif etype in ("user_injected", "turn_settled"):
                    # Chained/queued follow-up (user_injected) or end of a
                    # turn (turn_settled): finalize the current message and
                    # let the next turn render into a fresh one.
                    if acc.strip():
                        await self._finalize_reply(chat_id, thread_id, msg_id, acc)
                    acc, msg_id = "", 0

                elif etype == "error":
                    detail = ev.get("detail") or "unexpected error"
                    acc = f"{acc}\n\n⚠️ {detail}" if acc.strip() else f"⚠️ {detail}"

                # user_request / tool / queue events are handled elsewhere
                # or irrelevant to the reply text — skip them.
        finally:
            typing_stop.set()
            typing_task.cancel()
            try:
                await typing_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            try:
                if acc.strip():
                    await self._finalize_reply(chat_id, thread_id, msg_id, acc)
            except Exception:
                log.exception("telegram: final reply failed")
            self._streamers.pop(session_id, None)

    async def _finalize_reply(
        self, chat_id: int, thread_id: int, msg_id: int, text: str
    ) -> None:
        chunks = split_for_telegram(md_to_telegram_html(text))
        if not chunks:
            return
        if msg_id:
            ok = await self.client.edit_text_safe(chat_id, msg_id, chunks[0])
            if not ok:  # original deleted → send instead
                await self.client.send_text_safe(
                    chat_id, chunks[0], thread_id=thread_id or None
                )
        else:
            await self.client.send_text_safe(
                chat_id, chunks[0], thread_id=thread_id or None
            )
        for chunk in chunks[1:]:
            await self.client.send_text_safe(
                chat_id, chunk, thread_id=thread_id or None
            )

    async def _typing_loop(
        self, session_id: str, chat_id: int, thread_id: int, stop: asyncio.Event
    ) -> None:
        from ..server.services.chat_turn_runner import get_running_turn

        while not stop.is_set():
            if get_running_turn(session_id) is not None:
                await self.client.send_chat_action(
                    chat_id, "typing", thread_id=thread_id or None
                )
            try:
                await asyncio.wait_for(stop.wait(), timeout=_TYPING_INTERVAL)
            except asyncio.TimeoutError:
                pass

    # ── Callback queries (inline buttons) ────────────────────────────────

    async def _on_callback(self, cq: dict) -> None:
        from_user = cq.get("from") or {}
        user_id = int(from_user.get("id") or 0)
        if user_id not in (self.cfg.allowed_user_ids or []):
            await self.client.answer_callback_query(cq.get("id", ""), "Not authorized")
            return

        data = str(cq.get("data") or "")
        msg = cq.get("message") or {}
        chat_id = int(msg.get("chat", {}).get("id", 0))
        thread_id = int(msg.get("message_thread_id") or 0)
        message_id = int(msg.get("message_id") or 0)

        if data.startswith("sw:"):
            await self._on_switch(chat_id, thread_id, message_id, data[3:], cq)
        elif data.startswith("pj:"):
            info = MsgInfo(
                chat_id=chat_id,
                thread_id=thread_id,
                chat_type=msg.get("chat", {}).get("type", ""),
                user_id=user_id,
                user_label="",
            )
            await self.client.answer_callback_query(cq.get("id", ""), "Binding…")
            from .commands import cmd_project

            await cmd_project(self.deps, info, data[3:])
        elif data.startswith("hb:"):
            await self._on_hitl_answer(data[3:], cq)
        else:
            await self.client.answer_callback_query(cq.get("id", ""))

    async def _on_switch(
        self,
        chat_id: int,
        thread_id: int,
        message_id: int,
        session_id: str,
        cq: dict,
    ) -> None:
        binding = self.bindings.get(chat_id, thread_id)
        if binding is None:
            await self.client.answer_callback_query(cq.get("id", ""), "No binding")
            return
        session = self.store.get(session_id)
        if session is None:
            await self.client.answer_callback_query(cq.get("id", ""), "Chat not found")
            return
        self.bindings.set_active_session(chat_id, thread_id, session_id)
        await self.client.answer_callback_query(
            cq.get("id", ""), f"Switched to {session.title[:120]}"
        )
        # Re-render the chat list message with the ● marker moved.
        from .commands import _list_sessions

        sessions = await _list_sessions(self.deps, binding)
        rows = [
            [
                {
                    "text": (
                        f"{'● ' if s.id == session_id else ''}"
                        f"{(s.title or 'New session')[:56]}"
                    ),
                    "callback_data": f"sw:{s.id[:56]}",
                }
            ]
            for s in sessions
        ]
        if rows:
            try:
                await self.client.edit_text_safe(
                    chat_id,
                    message_id,
                    "Chats — tap to switch (● active):",
                    reply_markup={"inline_keyboard": rows},
                )
            except Exception:
                log.debug("telegram: chat list re-render failed", exc_info=True)

    # ── HITL ─────────────────────────────────────────────────────────────

    def build_hitl_keyboard(
        self, session_id: str, request_id: str, data: dict
    ) -> tuple[list[list[dict]], None] | tuple[None, str]:
        """Build buttons for a user_request; returns (keyboard, error_reason).

        Choice/confirm kinds get buttons; text/form kinds can't be answered
        from Telegram (free-form input) — the prompt is still forwarded with
        a note to answer in the Nexus UI.
        """
        kind = data.get("kind", "confirm")
        choices = data.get("choices") or []

        if kind == "choice" and choices:
            options = [str(c) for c in choices]
        elif kind == "confirm":
            options = ["yes", "no"]
        else:
            return None, "answer in the Nexus UI"

        rows = []
        for opt in options:
            key = uuid4().hex[:12]
            self._hitl_buttons[key] = (session_id, request_id, opt)
            rows.append([{"text": opt[:60], "callback_data": f"hb:{key}"}])
        return rows, None

    def register_hitl_message(
        self, request_id: str, chat_id: int, thread_id: int, message_id: int
    ) -> None:
        self._hitl_messages[request_id] = (chat_id, thread_id, message_id)

    async def _on_hitl_answer(self, key: str, cq: dict) -> None:
        entry = self._hitl_buttons.pop(key, None)
        if entry is None:
            await self.client.answer_callback_query(
                cq.get("id", ""), "Already answered"
            )
            return
        session_id, request_id, answer = entry
        # Sibling buttons for the same request are dead now — drop them.
        self._hitl_buttons = {
            k: v for k, v in self._hitl_buttons.items() if v[1] != request_id
        }

        resolved = self.store.resolve_pending(session_id, request_id, answer)
        if resolved:
            await self.client.answer_callback_query(cq.get("id", ""), "Answered")
        else:
            parked = self.store.get_hitl_pending(request_id)
            if parked is not None and parked.get("status") == "parked":
                await self.client.answer_callback_query(
                    cq.get("id", ""), "This prompt parked — answer it in the Nexus UI"
                )
            else:
                await self.client.answer_callback_query(
                    cq.get("id", ""), "Expired or already answered"
                )

        # Update the forwarded prompt message with the outcome.
        fwd = self._hitl_messages.pop(request_id, None)
        if fwd:
            chat_id, thread_id, message_id = fwd
            try:
                await self.client.edit_text_safe(
                    chat_id,
                    message_id,
                    f"✅ Answered: <b>{answer}</b>",
                )
            except Exception:
                log.debug("telegram: hitl message edit failed", exc_info=True)
