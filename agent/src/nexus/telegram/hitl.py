"""HITL forwarder — ask_user prompts as native Telegram interactions.

Subscribes to the session store's global HITL channel. When a
``user_request`` (ask_user / terminal / page approval) fires on a session
that belongs to a Telegram binding, the prompt is forwarded to the bound
chat with the input surface that fits its kind:

- confirm / choice → inline keyboard buttons (one tap answers);
- text → ForceReply — tapping the prompt opens a quoted reply box, and
  the chat's next plain message resolves the request;
- form (no secret fields) → a rendered field list + ForceReply, or a
  reply keyboard when the form is a single boolean/select;
- form with ``secret: true`` fields → a note to answer in the Nexus UI
  (secrets must not travel through Telegram's cloud chat history).

Answers resolve through the router (``store.resolve_pending`` — the same
primitive ``POST /chat/{sid}/respond`` uses), so a Telegram answer and a
UI answer race exactly like two browser tabs. Parked requests (the turn
already ended waiting) are resumed via the shared
``services/hitl_resume`` pipeline and streamed back like any other turn.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)


class HitlForwarder:
    def __init__(self, router) -> None:
        self._router = router
        self._store = router.store
        self._client = router.client
        self._bindings = router.bindings
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.get_running_loop().create_task(self._run())
        log.info("telegram: HITL forwarder started")

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _run(self) -> None:
        try:
            async for session_id, event in self._store.subscribe_global():
                kind = getattr(event, "kind", "")
                data = getattr(event, "data", {}) or {}
                try:
                    if kind == "user_request":
                        await self._forward(session_id, data)
                    elif kind == "user_request_cancelled":
                        await self._cancelled(data)
                except Exception:
                    log.exception("telegram: HITL forward failed")
        except Exception:
            log.exception("telegram: HITL forwarder loop died")

    async def _forward(self, session_id: str, data: dict) -> None:
        binding = self._bindings.find_by_session(session_id)
        if binding is None:
            return
        request_id = str(data.get("request_id") or "")
        prompt = str(data.get("prompt") or "").strip() or "(no prompt)"
        kind = str(data.get("kind") or "confirm")
        default = data.get("default")

        lines = [f"❓ <b>{prompt}</b>"]
        if default:
            lines.append(f"(default: {default})")

        markup, note, slot = self._router.build_hitl_interaction(
            session_id, request_id, data
        )

        if kind == "form" and slot is not None:
            from .forms import render_form_prompt

            rendered = render_form_prompt(
                data.get("fields") or [],
                title=data.get("form_title"),
                description=data.get("form_description"),
            )
            if rendered:
                lines.append(rendered)

        if note:
            lines.append(f"\nℹ️ This needs free-form input — {note}.")
        elif slot is not None:
            if slot.kind == "form" and slot.reply_keyboard:
                lines.append("💬 Tap a button or type your answer.")
            elif slot.kind == "form":
                lines.append(
                    "💬 Copy the 📋 template, fill the gaps, send it back "
                    "(or write <code>field: value</code> lines yourself)."
                )
            else:
                lines.append("💬 Reply here to answer.")

        reply_markup = markup
        try:
            message_id = await self._client.send_text_safe(
                binding.chat_id,
                "\n".join(lines),
                thread_id=binding.thread_id or None,
                reply_markup=reply_markup,
            )
        except Exception:
            log.exception("telegram: forwarding HITL prompt failed")
            return
        if message_id:
            self._router.register_hitl_message(
                request_id, binding.chat_id, binding.thread_id, message_id
            )
            if slot is not None:
                self._router.register_pending_answer(
                    binding.chat_id, binding.thread_id, slot, message_id
                )
        log.info(
            "telegram: forwarded HITL %s request %s (kind=%s, slot=%s)",
            request_id[:8],
            session_id[:8],
            kind,
            slot is not None,
        )

    async def _cancelled(self, data: dict) -> None:
        request_id = str(data.get("request_id") or "")
        router = self._router
        # The prompt is gone (timeout / superseded / answered elsewhere) —
        # its answer slot must not swallow the chat's next message.
        router.drop_pending_answer(request_id)
        fwd = router._hitl_messages.pop(request_id, None)
        if fwd is None:
            return
        chat_id, _thread_id, message_id = fwd
        try:
            await self._client.edit_text_safe(
                chat_id, message_id, "⏱ Expired — the prompt timed out."
            )
        except Exception:
            log.debug("telegram: hitl expiry edit failed", exc_info=True)
