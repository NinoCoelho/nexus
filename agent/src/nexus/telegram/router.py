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
import json
import logging
import re
import time
from dataclasses import dataclass
from html import escape as html_escape
from uuid import uuid4

from ..server.services.turn_launcher import launch_turn
from .api import TelegramClient, TelegramError
from .bindings import TelegramBindingStore
from .commands import COMMANDS, CommandDeps, MsgInfo
from .formatting import extract_vault_links, md_to_telegram_html, split_for_telegram

log = logging.getLogger(__name__)

_EDIT_INTERVAL = 2.5  # min seconds between progressive message edits
_TYPING_INTERVAL = 4.5
_DENY_THROTTLE = 60.0  # seconds between "not authorized" replies per user
_ACK_DONE_EMOJI = "👍"  # reaction upgrade when the turn finishes successfully
_MAX_PENDING_ACKS = 50  # cap per session; acks are cosmetic — bound memory
_MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # Telegram bot download cap
_MAX_PENDING_ANSWERS = 32  # free-form answer slots; bounded, oldest evicted
_COMMAND_RE = re.compile(r"^/([a-zA-Z_]+)(@\w+)?\s*(.*)$", re.S)
_ECHO_TRUNCATE = 600  # cap for the echoed web user message (chars)


def _web_echo_text(message: str) -> str:
    """Format a web-originated user message as the echo bubble (markdown)."""
    text = (message or "").strip()
    if not text:
        text = "*(attachment)*"
    if len(text) > _ECHO_TRUNCATE:
        text = text[:_ECHO_TRUNCATE].rstrip() + "…"
    quoted = "\n".join(f"> {line}" if line.strip() else ">" for line in text.splitlines())
    return f"💬 **You, via web**\n\n{quoted}"


@dataclass
class PendingAnswer:
    """A free-form ask_user prompt awaiting the chat's next reply.

    Registered when a ``text``/``form`` prompt is forwarded with a
    ForceReply / reply-keyboard markup; consumed by the next plain
    message (or a reply quoting the prompt bubble).
    """

    session_id: str
    request_id: str
    kind: str  # "text" | "form"
    fields: list[dict] | None = None
    # Telegram message_id of the forwarded prompt bubble — binds quoted
    # replies to this exact prompt.
    message_id: int = 0
    # True when the prompt sent a reply keyboard (collapsed via
    # remove_keyboard once the answer lands).
    reply_keyboard: bool = False


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
            router=self,
        )
        # session_id → live reply-streamer task
        self._streamers: dict[str, asyncio.Task] = {}
        # (chat_id, message_id) → vault paths offered by that message's 📂
        # buttons (reply menus from the streamer, listings from /vault).
        # Bounded — oldest entries evicted.
        self._vault_menus: dict[tuple[int, int], list[str]] = {}
        # user_id → last deny-reply timestamp
        self._deny_last: dict[int, float] = {}
        # short key → (session_id, request_id, answer) for HITL buttons
        self._hitl_buttons: dict[str, tuple[str, str, str]] = {}
        # request_id → (chat_id, thread_id, message_id) for forwarded prompts
        self._hitl_messages: dict[str, tuple[int, int, int]] = {}
        # (chat_id, thread_id) → free-form prompt awaiting this chat's reply
        self._pending_answers: dict[tuple[int, int], PendingAnswer] = {}
        # session_id → parked-resume task (answers to parked prompts)
        self._resume_tasks: dict[str, asyncio.Task] = {}
        # session_id → [(chat_id, message_id)] of acknowledged-but-unanswered
        # user messages. 👀 on receipt; upgraded to 👍 when the turn settles.
        self._pending_acks: dict[str, list[tuple[int, int]]] = {}
        # Sessions whose next settled turn should get a voice-note reply
        # (set when the trigger message was a voice note).
        self._voice_reply_sessions: set[str] = set()

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
        media, voice_note = self._extract_media(msg)
        if not text and not media and voice_note is None:
            return  # service messages (topic created, joins, stickers, …)

        chat = msg.get("chat", {})
        from_user = msg.get("from") or {}
        chat_id = int(chat.get("id", 0))
        chat_type = str(chat.get("type", ""))
        thread_id = int(msg.get("message_thread_id") or 0)
        user_id = int(from_user.get("id", 0))
        first = str(from_user.get("first_name") or from_user.get("title") or "")
        username = str(from_user.get("username") or "")
        label = f"{first} (@{username})" if username else (first or f"user {user_id}")
        reply_to = msg.get("reply_to_message") or {}

        info = MsgInfo(
            chat_id=chat_id,
            thread_id=thread_id,
            chat_type=chat_type,
            user_id=user_id,
            user_label=label,
            message_id=int(msg.get("message_id") or 0),
            reply_to_message_id=int(reply_to.get("message_id") or 0),
        )

        if user_id not in (self.cfg.allowed_user_ids or []):
            await self._deny(user_id, chat_id, thread_id)
            return

        if text.startswith("/") and not media and voice_note is None:
            await self._dispatch_command(text, info)
        elif voice_note is not None:
            await self._handle_voice_message(voice_note, text, chat_type, info)
        else:
            await self._handle_chat_message(text, chat_type, info, media=media)

    async def _deny(self, user_id: int, chat_id: int, thread_id: int) -> None:
        log.info(
            "telegram: unauthorized message from user %s in chat %s (thread %s) — "
            "not in allowed_user_ids",
            user_id,
            chat_id,
            thread_id,
        )
        if not self.cfg.deny_message:
            return
        now = time.monotonic()
        if now - self._deny_last.get(user_id, 0.0) < _DENY_THROTTLE:
            return
        self._deny_last[user_id] = now
        try:
            await self.client.send_text_safe(
                chat_id,
                "⛔ You are not authorized to use this bot.\n"
                f"Your Telegram id: <code>{user_id}</code> — if you're the owner, "
                "add it under Settings → Features → Telegram → Allowed users.",
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

    # ── Media (photos / files) and voice notes ───────────────────────────

    @staticmethod
    def _extract_media(msg: dict) -> tuple[list[dict], dict | None]:
        """Pull downloadable media out of a message payload.

        Returns ``(media_list, voice_note)``. ``voice_note`` is a separate
        return because voice is transcribed into the turn text rather than
        attached. Stickers/animations are ignored (no useful content).
        """
        media: list[dict] = []
        voice: dict | None = None

        photo = msg.get("photo")
        if isinstance(photo, list) and photo:
            largest = photo[-1]  # sizes are ordered ascending
            media.append(
                {
                    "file_id": largest.get("file_id"),
                    "mime": "image/jpeg",
                    "name": "photo.jpg",
                    "size": int(largest.get("file_size") or 0),
                }
            )

        for key, default_mime, default_name in (
            ("document", None, None),
            ("video", "video/mp4", "video.mp4"),
            ("audio", "audio/mpeg", "audio.mp3"),
            ("video_note", "video/mp4", "video_note.mp4"),
        ):
            obj = msg.get(key)
            if isinstance(obj, dict) and obj.get("file_id"):
                media.append(
                    {
                        "file_id": obj["file_id"],
                        "mime": obj.get("mime_type") or default_mime or "",
                        "name": obj.get("file_name") or default_name or key,
                        "size": int(obj.get("file_size") or 0),
                    }
                )

        v = msg.get("voice")
        if isinstance(v, dict) and v.get("file_id"):
            voice = {
                "file_id": v["file_id"],
                "mime": v.get("mime_type") or "audio/ogg",
                "duration": int(v.get("duration") or 0),
            }
        return media, voice

    async def _download_media(self, file_id: str, declared_size: int) -> bytes:
        """Download a Telegram file, enforcing the 20 MB bot cap."""
        if declared_size and declared_size > _MAX_DOWNLOAD_BYTES:
            raise TelegramError(
                f"file too large ({declared_size / 1024 / 1024:.0f} MB; "
                "Telegram bot downloads cap at 20 MB)"
            )
        info = await self.client.get_file(file_id)
        size = int(info.get("file_size") or 0)
        if size and size > _MAX_DOWNLOAD_BYTES:
            raise TelegramError("file too large (over Telegram's 20 MB bot cap)")
        return await self.client.download_file(info["file_path"])

    async def _ingest_media(self, media: list[dict], info: MsgInfo) -> list | None:
        """Download each media item into the vault, returning ContentParts.

        None (after a ⚠️ bubble) when nothing could be ingested — the turn
        is not started so the user immediately knows why.
        """
        from .. import vault
        from ..agent.llm import ContentPart
        from ..multimodal import sniff_mime

        parts: list = []
        for m in media:
            file_id = m.get("file_id")
            if not file_id:
                continue
            try:
                data = await self._download_media(file_id, int(m.get("size") or 0))
            except Exception as exc:
                await self.client.send_text_safe(
                    info.chat_id,
                    f"⚠️ Couldn't fetch {m.get('name', 'attachment')}: {exc}",
                    thread_id=info.thread_id or None,
                )
                continue
            name = re.sub(r"[^\w.\-]+", "_", str(m.get("name") or "file")).strip("._") or "file"
            rel = f"uploads/telegram/{int(time.time() * 1000)}_{name}"
            try:
                vault.write_file_bytes(rel, data)
            except Exception as exc:
                await self.client.send_text_safe(
                    info.chat_id,
                    f"⚠️ Couldn't store {name}: {exc}",
                    thread_id=info.thread_id or None,
                )
                continue
            mime = m.get("mime") or sniff_mime(rel)
            if mime.startswith("image/"):
                kind = "image"
            elif mime.startswith("audio/"):
                kind = "audio"
            else:
                kind = "document"
            parts.append(
                ContentPart(kind=kind, vault_path=rel, mime_type=mime)  # type: ignore[arg-type]
            )

        if not parts:
            return None
        return parts

    async def _handle_voice_message(
        self, voice: dict, caption: str, chat_type: str, info: MsgInfo
    ) -> None:
        """Voice note → transcript → normal chat turn (+ voice reply)."""
        from ..multimodal import transcribe_bytes

        await self.client.send_chat_action(info.chat_id, "typing", thread_id=info.thread_id or None)
        try:
            audio = await self._download_media(voice["file_id"], 0)
            transcript = await transcribe_bytes(audio, voice.get("mime") or "audio/ogg")
        except Exception as exc:
            await self.client.send_text_safe(
                info.chat_id,
                f"⚠️ Couldn't process the voice message: {exc}",
                thread_id=info.thread_id or None,
            )
            return

        transcript = (transcript or "").strip()
        if not transcript:
            await self.client.send_text_safe(
                info.chat_id,
                "⚠️ Couldn't transcribe the voice message (transcription may "
                "be unavailable — check Settings → Features → Transcription).",
                thread_id=info.thread_id or None,
            )
            return

        text = transcript if not caption else f"{transcript}\n\n{caption}"
        await self._handle_chat_message(
            text, chat_type, info, media=None, voice_reply=self.cfg.voice_replies
        )

    # ── Chat messages ────────────────────────────────────────────────────

    async def _handle_chat_message(
        self,
        text: str,
        chat_type: str,
        info: MsgInfo,
        *,
        media: list[dict] | None = None,
        voice_reply: bool = False,
    ) -> None:
        # A pending free-form prompt owns this chat: the next plain text
        # (typed reply or transcribed voice note) answers it instead of
        # starting a turn. Media falls through — it queues behind the
        # blocked turn like any other message.
        if not media and text.strip():
            if await self._try_answer_pending(text, info):
                return

        binding = self.bindings.get(info.chat_id, info.thread_id)

        if binding is None:
            if chat_type == "private":
                # Owner DM → the coordinator master chat when enabled; the
                # DM becomes the always-on deputy instead of a throwaway
                # unprojected session.
                coordinator = self._coordinator_service()
                if coordinator is not None:
                    sid = coordinator.ensure_session()
                    binding = self.bindings.upsert(
                        chat_id=info.chat_id,
                        thread_id=info.thread_id,
                        kind="dm",
                        project_id=None,
                        active_session_id=sid,
                    )
                else:
                    binding = await self._create_binding(info, project_id=None)
            else:
                # Forum topic or plain group — standalone chat, no project
                # needed. Topics chat right away (silent); /project can
                # attach one later. Plain groups keep the one-time notice.
                binding = await self._create_binding(info, project_id=None)
                if not info.thread_id:
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

        # Media ingestion: download → vault → ContentParts. On total failure
        # the turn isn't started (the user got a ⚠️ bubble per file).
        attachment_parts: list | None = None
        if media:
            attachment_parts = await self._ingest_media(media, info)
            if attachment_parts is None:
                return
            if not text.strip() and not attachment_parts:
                return

        # Group messages carry the sender so the agent knows who's talking.
        message = text
        if chat_type != "private":
            message = (
                f"From {info.user_label}:\n\n{text}"
                if text.strip()
                else (f"From {info.user_label} (attachment):")
            )

        # Ack via reaction (👀) BEFORE processing starts — the user sees the
        # like immediately, and a failure in the turn's pre-flight can never
        # silently swallow the ack. Tracked so the streamer can upgrade to 👍
        # when the turn settles successfully (errored turns keep the 👀).
        self._track_ack(session.id, info)
        if self.cfg.ack_reaction:
            log.info(
                "telegram: ack %s on msg %s (chat %s, session %.8s)",
                self.cfg.ack_reaction,
                info.message_id,
                info.chat_id,
                session.id,
            )
            await self.client.set_message_reaction(
                info.chat_id, info.message_id, self.cfg.ack_reaction
            )
        if voice_reply:
            self._voice_reply_sessions.add(session.id)

        outcome = await launch_turn(
            agent=self.agent,
            store=self.store,
            tracker=self.tracker,
            session=session,
            message=message,
            attachment_parts=attachment_parts,
            publish_job_event=self.publish_job_event,
            # Title from the raw text, without the "From <sender>:" prefix.
            autotitle_message=text.strip() or None,
            origin="telegram",
        )

        if outcome.error is not None:
            await self.client.send_text_safe(
                info.chat_id,
                f"⚠️ {outcome.error}",
                thread_id=info.thread_id or None,
            )
            return

        # Queued: a streamer should already be alive; if it died (idle exit
        # / crash), spawn one so the answer still lands.
        self._ensure_streamer(session.id, info.chat_id, info.thread_id)

    # ── Free-form HITL answers (text / form prompts) ─────────────────────

    async def _try_answer_pending(self, text: str, info: MsgInfo) -> bool:
        """Consume ``text`` as the answer to a pending free-form prompt.

        Returns True when the message was an answer (consumed — no turn
        starts). A reply quoting a *different* message is a reply to that
        thing, not the prompt — it flows through to the chat normally.
        """
        key = (info.chat_id, info.thread_id)
        slot = self._pending_answers.get(key)
        if slot is None:
            return False
        if (
            info.reply_to_message_id
            and slot.message_id
            and info.reply_to_message_id != slot.message_id
        ):
            return False

        if slot.kind == "form":
            from .forms import format_answer_for_display, parse_form_reply

            answer, errors = parse_form_reply(text, slot.fields or [])
            if errors:
                await self.client.send_text_safe(
                    info.chat_id,
                    "⚠️ " + "\n".join(errors)[:800] + "\n\nThe question is still "
                    "waiting — reply again (or /cancel to dismiss it).",
                    thread_id=info.thread_id or None,
                )
                return True
            await self._resolve_answer(
                slot, key, answer, display=format_answer_for_display(answer)
            )
        else:
            display = text.strip()
            await self._resolve_answer(slot, key, display, display=display)
        return True

    async def _resolve_answer(
        self,
        slot: PendingAnswer,
        key: tuple[int, int],
        answer: object,
        *,
        display: str,
    ) -> None:
        """Resolve a pending prompt: live future → parked resume → gone."""
        raw = (
            json.dumps(answer, ensure_ascii=False)
            if not isinstance(answer, str)
            else answer
        )
        if self.store.resolve_pending(slot.session_id, slot.request_id, raw):
            self._pending_answers.pop(key, None)
            await self._edit_prompt_answered(slot, display)
            return

        row = self.store.get_hitl_pending(slot.request_id)
        if (
            row is not None
            and row.get("status") == "parked"
            and row.get("session_id") == slot.session_id
        ):
            self._pending_answers.pop(key, None)
            await self._edit_prompt_answered(slot, display)
            self._start_parked_resume(key[0], key[1], slot, answer)
            return

        # Answered from the web UI in the meantime, or timed out.
        self._pending_answers.pop(key, None)
        await self._edit_prompt_text(
            slot, "⏩ Already answered or expired."
        )

    def _start_parked_resume(
        self, chat_id: int, thread_id: int, slot: PendingAnswer, answer: object
    ) -> None:
        """Resume a parked turn from Telegram.

        The resumed turn's deltas reach the session bus via the ``_trace``
        hook (the resume service sets the session contextvar), so the
        long-lived reply streamer renders them like any other turn.
        """
        self._ensure_streamer(slot.session_id, chat_id, thread_id)
        task = asyncio.create_task(
            self._run_parked_resume(slot.session_id, slot.request_id, answer),
            name=f"tg-hitl-resume-{slot.request_id[:8]}",
        )
        self._cancel_resume_task(slot.session_id)
        self._resume_tasks[slot.session_id] = task
        log.info(
            "telegram: resuming parked HITL %s (session %.8s) from telegram",
            slot.request_id[:8],
            slot.session_id,
        )

    async def _run_parked_resume(
        self, session_id: str, request_id: str, answer: object
    ) -> None:
        from ..server.services.hitl_resume import drive_parked_resume, prepare_parked_resume

        try:
            prepared = await prepare_parked_resume(
                self.store,
                session_id=session_id,
                request_id=request_id,
                raw_answer=answer,
            )
            if prepared is None or prepared.duplicate:
                return
            async for _event in drive_parked_resume(
                self.agent, self.store, prepared, task_registry=self._resume_tasks
            ):
                pass  # events flow to subscribers via the session bus
        except Exception:
            log.exception("telegram: parked resume failed")

    def _cancel_resume_task(self, session_id: str) -> bool:
        task = self._resume_tasks.get(session_id)
        if task is not None and not task.done():
            task.cancel()
            return True
        return False

    def cancel_resume(self, session_id: str) -> bool:
        """Cancel a Telegram-initiated parked-resume turn (/cancel)."""
        return self._cancel_resume_task(session_id)

    async def _edit_prompt_answered(self, slot: PendingAnswer, display: str) -> None:
        shown = html_escape(display).replace("\n", " · ")
        await self._edit_prompt_text(
            slot, f"✅ Answered: <b>{shown[:300]}</b>"
        )

    async def _edit_prompt_text(self, slot: PendingAnswer, html_text: str) -> None:
        fwd = self._hitl_messages.get(slot.request_id)
        if fwd is None:
            return
        chat_id, _thread_id, message_id = fwd
        markup = {"remove_keyboard": True} if slot.reply_keyboard else None
        try:
            await self.client.edit_text_safe(
                chat_id, message_id, html_text, reply_markup=markup
            )
        except Exception:
            log.debug("telegram: hitl prompt edit failed", exc_info=True)

    def register_pending_answer(
        self, chat_id: int, thread_id: int, slot: PendingAnswer, message_id: int
    ) -> None:
        slot.message_id = message_id
        self._pending_answers[(chat_id, thread_id)] = slot
        while len(self._pending_answers) > _MAX_PENDING_ANSWERS:
            self._pending_answers.pop(next(iter(self._pending_answers)))

    def drop_pending_answer(self, request_id: str) -> None:
        """Forget the answer slot for a request that expired/was answered."""
        for key, slot in list(self._pending_answers.items()):
            if slot.request_id == request_id:
                self._pending_answers.pop(key, None)

    async def dismiss_pending_answer(self, info: MsgInfo) -> PendingAnswer | None:
        """/cancel — dismiss this chat's pending question, if any.

        Mirrors the web bell cancel: drop the broker future or parked
        row, broadcast ``user_request_cancelled`` (every surface updates),
        and mark the bubble cancelled. Returns the dismissed slot so the
        caller can also stop the turn that was waiting on it.
        """
        key = (info.chat_id, info.thread_id)
        slot = self._pending_answers.get(key)
        if slot is None:
            return None
        self._pending_answers.pop(key, None)

        from ..server.events import SessionEvent

        cancelled_live = self.store.cancel_pending(slot.session_id, slot.request_id)
        cancelled_parked = False
        if not cancelled_live:
            cancelled_parked = self.store.cancel_hitl_pending(
                slot.request_id, reason="user_cancelled"
            )
        if cancelled_live or cancelled_parked:
            try:
                self.store.publish(
                    slot.session_id,
                    SessionEvent(
                        kind="user_request_cancelled",
                        data={
                            "request_id": slot.request_id,
                            "reason": "user_cancelled",
                        },
                    ),
                )
            except Exception:  # noqa: BLE001 — best-effort
                log.exception("telegram: publishing hitl cancellation failed")
        await self._edit_prompt_text(slot, "🚫 Cancelled.")
        return slot

    def _track_ack(self, session_id: str, info: MsgInfo) -> None:
        if not info.message_id or not self.cfg.ack_reaction:
            return
        pending = self._pending_acks.setdefault(session_id, [])
        pending.append((info.chat_id, info.message_id))
        if len(pending) > _MAX_PENDING_ACKS:
            del pending[: len(pending) - _MAX_PENDING_ACKS]

    async def _upgrade_acks(self, session_id: str) -> None:
        """Swap tracked 👀 acks to 👍 after a turn settles successfully.

        Called from the streamer. If another turn is already running on the
        session (back-to-back launch), the list is kept — the next settle
        will upgrade everything (never a premature 👍).
        """
        from ..server.services.chat_turn_runner import get_running_turn

        if get_running_turn(session_id) is not None:
            return
        pending = self._pending_acks.pop(session_id, [])
        for chat_id, message_id in pending:
            await self.client.set_message_reaction(chat_id, message_id, _ACK_DONE_EMOJI)

    def _session_context(self, info: MsgInfo) -> str:
        if info.chat_type == "private":
            return f"Telegram: dm {info.chat_id}"
        if info.thread_id:
            return f"Telegram: topic {info.chat_id}/{info.thread_id}"
        return f"Telegram: group {info.chat_id}"

    def _coordinator_service(self):
        """The coordinator service when [coordinator] is enabled."""
        try:
            from ..coordinator import get_service

            svc = get_service()
            if svc is None or not svc.config.enabled:
                return None
            return svc
        except Exception:
            return None

    async def _create_binding(self, info: MsgInfo, *, project_id: str | None):
        kind = "dm" if info.chat_type == "private" else ("topic" if info.thread_id else "group")
        session = self.store.create(context=self._session_context(info), project_id=project_id)
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

    def _ensure_streamer(self, session_id: str, chat_id: int, thread_id: int) -> None:
        existing = self._streamers.get(session_id)
        if existing is not None and not existing.done():
            return
        self._streamers[session_id] = asyncio.create_task(
            self._stream_reply(session_id, chat_id, thread_id),
            name=f"telegram-reply-{session_id[:8]}",
        )

    def ensure_session_streamer(self, session_id: str) -> bool:
        """Spawn the reply streamer when ``session_id`` is Telegram-bound.

        Entry point for the web chat path: when a web-originated turn
        starts on a bound session, this guarantees the streamed reply —
        and the user-message echo — reach the bound chat even if no
        Telegram activity ever spawned a streamer (server restart, poller
        rebuild, fresh binding via PATCH). Honors ``[telegram].web_sync``.
        Best-effort; never raises.
        """
        if not getattr(self.cfg, "web_sync", True):
            return False
        try:
            binding = self.bindings.find_by_session(session_id)
        except Exception:
            log.debug("telegram: web-sync binding lookup failed", exc_info=True)
            return False
        if binding is None:
            return False
        self._ensure_streamer(session_id, binding.chat_id, binding.thread_id)
        return True

    async def aclose(self) -> None:
        """Cancel all reply streamers + resume tasks (poller shutdown)."""
        for t in list(self._streamers.values()):
            if not t.done():
                t.cancel()
        for t in list(self._streamers.values()) + list(self._resume_tasks.values()):
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._streamers.clear()
        self._resume_tasks.clear()

    async def _maybe_send_echo(self, chat_id: int, thread_id: int, text: str) -> int:
        """Echo a web-originated user message; returns the echo message id.

        The turn's reply is then sent as a Telegram reply to that echo,
        giving the chat a quote thread: user message → quoted response.
        """
        if not (text or "").strip():
            return 0
        try:
            return await self.client.send_text_safe(
                chat_id,
                md_to_telegram_html(_web_echo_text(text)),
                thread_id=thread_id or None,
            )
        except Exception:  # noqa: BLE001 — echo is best-effort
            log.exception("telegram: web-turn echo failed")
            return 0

    async def _stream_reply(self, session_id: str, chat_id: int, thread_id: int) -> None:
        acc = ""
        msg_id = 0
        last_edit = 0.0
        # Echo bubble for a web-originated turn: the streamed reply quotes
        # it (reply_to_message_id) so Telegram shows user message → response.
        echo_msg_id = 0
        # web_sync off: skip rendering this web-originated turn entirely.
        suppress_turn = False
        turn_errored = False  # error seen in the current turn — keep its 👀 ack
        typing_stop = asyncio.Event()
        typing_task = asyncio.create_task(
            self._typing_loop(session_id, chat_id, thread_id, typing_stop)
        )
        try:
            async for sevent in self.store.subscribe_with_replay(session_id):
                ev = sevent.data if hasattr(sevent, "data") else sevent
                etype = ev.get("type")

                if etype == "delta":
                    if suppress_turn:
                        continue
                    acc += ev.get("text", "")
                    now = time.monotonic()
                    if msg_id == 0 and acc.strip():
                        msg_id = await self.client.send_text_safe(
                            chat_id,
                            md_to_telegram_html(acc),
                            thread_id=thread_id or None,
                            reply_to_message_id=echo_msg_id or None,
                        )
                        last_edit = now
                    elif msg_id and self.cfg.stream_edits and now - last_edit >= _EDIT_INTERVAL:
                        ok = await self.client.edit_text_safe(
                            chat_id, msg_id, md_to_telegram_html(acc)
                        )
                        if not ok:  # original message was deleted
                            msg_id = await self.client.send_text_safe(
                                chat_id,
                                md_to_telegram_html(acc),
                                thread_id=thread_id or None,
                                reply_to_message_id=echo_msg_id or None,
                            )
                        last_edit = now

                elif etype == "turn_started":
                    # Fresh turn: finalize any leftover partial (missed
                    # settle) and reset. Web-originated turns echo the
                    # user's message first; the reply quotes that echo.
                    # With web_sync off, web turns render nowhere.
                    if acc.strip():
                        await self._finalize_reply(chat_id, thread_id, msg_id, acc)
                    acc, msg_id, echo_msg_id = "", 0, 0
                    suppress_turn = False
                    if ev.get("origin") == "web":
                        if getattr(self.cfg, "web_sync", True):
                            echo_msg_id = await self._maybe_send_echo(
                                chat_id, thread_id, ev.get("message", "")
                            )
                        else:
                            suppress_turn = True

                elif etype in ("user_injected", "turn_settled"):
                    # Chained/queued follow-up (user_injected) or end of a
                    # turn (turn_settled): finalize the current message and
                    # let the next turn render into a fresh one.
                    if acc.strip():
                        await self._finalize_reply(
                            chat_id, thread_id, msg_id, acc,
                            reply_to_message_id=echo_msg_id or None,
                        )
                    reply_text = acc
                    acc, msg_id, echo_msg_id = "", 0, 0
                    suppress_turn = False
                    if (
                        etype == "user_injected"
                        and ev.get("origin") == "web"
                        and getattr(self.cfg, "web_sync", True)
                    ):
                        # The injected message starts the next render —
                        # echo it so the chat keeps its quote thread.
                        echo_msg_id = await self._maybe_send_echo(
                            chat_id, thread_id, ev.get("text", "")
                        )
                    if etype == "turn_settled":
                        if not turn_errored:
                            await self._upgrade_acks(session_id)
                            if session_id in self._voice_reply_sessions:
                                self._voice_reply_sessions.discard(session_id)
                                # Detached: TTS synthesis can take seconds —
                                # don't hold the stream loop.
                                asyncio.create_task(
                                    self._send_voice_reply(chat_id, thread_id, reply_text)
                                )
                        turn_errored = False

                elif etype == "error":
                    if suppress_turn:
                        continue
                    detail = ev.get("detail") or "unexpected error"
                    turn_errored = True
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
                    await self._finalize_reply(
                        chat_id, thread_id, msg_id, acc,
                        reply_to_message_id=echo_msg_id or None,
                    )
            except Exception:
                log.exception("telegram: final reply failed")
            self._streamers.pop(session_id, None)

    async def _finalize_reply(
        self,
        chat_id: int,
        thread_id: int,
        msg_id: int,
        text: str,
        *,
        reply_to_message_id: int | None = None,
    ) -> None:
        chunks = split_for_telegram(md_to_telegram_html(text))
        if not chunks:
            return
        # Vault door: every vault:// link in the reply gets a 📂 button that
        # sends the file as a document.
        paths = extract_vault_links(text)
        kb: dict | None = None
        if paths:
            kb = {
                "inline_keyboard": [
                    [{"text": f"📂 {p.rsplit('/', 1)[-1][:56]}", "callback_data": f"vf:{i}"}]
                    for i, p in enumerate(paths)
                ]
            }
        first_id = 0
        if msg_id:
            ok = await self.client.edit_text_safe(chat_id, msg_id, chunks[0], reply_markup=kb)
            # The button lives on the message we just edited — register the
            # menu under THAT id (a resend on failed edit gets a fresh id).
            first_id = msg_id if ok else 0
            if not ok:  # original deleted → send instead
                first_id = await self.client.send_text_safe(
                    chat_id,
                    chunks[0],
                    thread_id=thread_id or None,
                    reply_markup=kb,
                    reply_to_message_id=reply_to_message_id,
                )
        else:
            first_id = await self.client.send_text_safe(
                chat_id,
                chunks[0],
                thread_id=thread_id or None,
                reply_markup=kb,
                reply_to_message_id=reply_to_message_id,
            )
        self._remember_vault_menu(chat_id, first_id, paths)
        for chunk in chunks[1:]:
            await self.client.send_text_safe(chat_id, chunk, thread_id=thread_id or None)

    async def _typing_loop(
        self, session_id: str, chat_id: int, thread_id: int, stop: asyncio.Event
    ) -> None:
        from ..server.services.chat_turn_runner import get_running_turn

        while not stop.is_set():
            if get_running_turn(session_id) is not None:
                await self.client.send_chat_action(chat_id, "typing", thread_id=thread_id or None)
            try:
                await asyncio.wait_for(stop.wait(), timeout=_TYPING_INTERVAL)
            except asyncio.TimeoutError:
                pass

    async def _send_voice_reply(self, chat_id: int, thread_id: int, text: str) -> None:
        """Synthesize the final reply and send it as a voice note.

        Best-effort: failures log and leave the text reply as the answer.
        """
        from .voice import deliver_voice_note

        try:
            await deliver_voice_note(
                self.client,
                chat_id,
                thread_id,
                text,
                tts_cfg=self._tts_cfg(),
                agent=self.agent,
                speechify_mode=getattr(self.cfg, "voice_speechify", "auto"),
            )
        except Exception:
            log.exception("telegram: voice reply failed")

    def _tts_cfg(self):
        try:
            from ..config_file import load_cached as load_config

            return load_config().tts
        except Exception:
            return None

    def _remember_vault_menu(self, chat_id: int, message_id: int, paths: list[str]) -> None:
        if message_id <= 0 or not paths:
            return
        self._vault_menus[(chat_id, message_id)] = paths
        while len(self._vault_menus) > 128:
            self._vault_menus.pop(next(iter(self._vault_menus)))

    async def _send_vault_file(self, chat_id: int, thread_id: int, path: str) -> str:
        """Send one vault file as a Telegram document. Returns a user-facing
        status line for the callback answer."""
        from ..vault import resolve_path

        try:
            full = resolve_path(path)
        except ValueError:
            return "Invalid path"
        if not full.is_file():
            return "File not found"
        if full.stat().st_size > 49 * 1024 * 1024:
            return "File too large for Telegram (50 MB cap)"
        try:
            data = full.read_bytes()
        except OSError:
            return "Couldn't read file"
        await self.client.send_document(
            chat_id,
            data,
            full.name,
            thread_id=thread_id or None,
            caption=path[:900],
        )
        return f"Sent {full.name}"

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
        elif data.startswith("vf:"):
            paths = self._vault_menus.get((chat_id, message_id))
            try:
                idx = int(data[3:])
                path = paths[idx] if paths else None
            except (ValueError, IndexError):
                path = None
            if path is None:
                await self.client.answer_callback_query(cq.get("id", ""), "Menu expired")
                return
            await self.client.send_chat_action(
                chat_id, "upload_document", thread_id=thread_id or None
            )
            status = await self._send_vault_file(chat_id, thread_id, path)
            await self.client.answer_callback_query(cq.get("id", ""), status)
        elif data.startswith("vd:"):
            from .commands import vault_browse

            try:
                idx = int(data[3:])
            except ValueError:
                idx = -1
            await vault_browse(self.deps, chat_id, thread_id, idx, message_id)
            await self.client.answer_callback_query(cq.get("id", ""))
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
                        f"{'● ' if s.id == session_id else ''}{(s.title or 'New session')[:56]}"
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

    def build_hitl_interaction(
        self, session_id: str, request_id: str, data: dict
    ) -> tuple[dict | None, str | None, PendingAnswer | None]:
        """Pick the Telegram input surface for a ``user_request``.

        Returns ``(reply_markup, note, pending_slot)``:

        - confirm / choice → inline keyboard buttons (one tap answers);
        - text → ForceReply: tapping the prompt opens a quoted reply box,
          the answer is bound to the prompt via ``reply_to_message``;
        - form without secret fields and exactly one boolean/select
          field → reply keyboard with the allowed values (tap = answer);
        - form without secret fields → ForceReply with a
          ``field: value`` placeholder;
        - form with ``secret: true`` fields → no markup: secrets typed
          into a Telegram chat would live in Telegram's cloud history —
          those stay answerable in the Nexus UI only.

        ``pending_slot`` is non-None when the answer arrives as the
        chat's next message; the forwarder registers it after sending.
        """
        kind = data.get("kind", "confirm")
        choices = data.get("choices") or []

        if kind == "choice" and choices:
            options = [str(c) for c in choices]
        elif kind == "confirm":
            options = ["yes", "no"]
        elif kind == "text":
            markup = {
                "force_reply": True,
                "input_field_placeholder": "Type your answer…",
            }
            return (
                markup,
                None,
                PendingAnswer(session_id=session_id, request_id=request_id, kind="text"),
            )
        elif kind == "form":
            from .forms import choice_values, has_secret_fields, single_choice_field

            fields = data.get("fields") or []
            if has_secret_fields(fields):
                return None, "answer in the Nexus UI (this form has secret fields)", None
            single = single_choice_field(fields)
            if single is not None:
                values = choice_values(single)
                markup = {
                    "keyboard": [[{"text": v} for v in values]],
                    "resize_keyboard": True,
                    "one_time_keyboard": True,
                }
                return (
                    markup,
                    None,
                    PendingAnswer(
                        session_id=session_id,
                        request_id=request_id,
                        kind="form",
                        fields=fields,
                        reply_keyboard=True,
                    ),
                )
            markup = {
                "force_reply": True,
                "input_field_placeholder": "field: value — one per line",
            }
            return (
                markup,
                None,
                PendingAnswer(
                    session_id=session_id, request_id=request_id, kind="form", fields=fields
                ),
            )
        else:
            return None, "answer in the Nexus UI", None

        rows = []
        for opt in options:
            key = uuid4().hex[:12]
            self._hitl_buttons[key] = (session_id, request_id, opt)
            rows.append([{"text": opt[:60], "callback_data": f"hb:{key}"}])
        return {"inline_keyboard": rows}, None, None

    def register_hitl_message(
        self, request_id: str, chat_id: int, thread_id: int, message_id: int
    ) -> None:
        self._hitl_messages[request_id] = (chat_id, thread_id, message_id)

    async def _on_hitl_answer(self, key: str, cq: dict) -> None:
        entry = self._hitl_buttons.pop(key, None)
        if entry is None:
            await self.client.answer_callback_query(cq.get("id", ""), "Already answered")
            return
        session_id, request_id, answer = entry
        # Sibling buttons for the same request are dead now — drop them.
        self._hitl_buttons = {k: v for k, v in self._hitl_buttons.items() if v[1] != request_id}

        msg = cq.get("message") or {}
        chat_id = int(msg.get("chat", {}).get("id", 0))
        thread_id = int(msg.get("message_thread_id") or 0)

        resolved = self.store.resolve_pending(session_id, request_id, answer)
        if resolved:
            await self.client.answer_callback_query(cq.get("id", ""), "Answered")
        else:
            # Parked (the turn ended waiting) — resume it from Telegram
            # through the same service the web resume route uses.
            row = self.store.get_hitl_pending(request_id)
            if (
                row is not None
                and row.get("status") == "parked"
                and row.get("session_id") == session_id
            ):
                await self.client.answer_callback_query(
                    cq.get("id", ""), "Answered — resuming"
                )
                self._start_parked_resume(
                    chat_id,
                    thread_id,
                    PendingAnswer(
                        session_id=session_id, request_id=request_id, kind="confirm"
                    ),
                    answer,
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
