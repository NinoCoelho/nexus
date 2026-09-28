"""HITL forwarder — ask_user prompts as Telegram inline keyboards.

Subscribes to the session store's global HITL channel. When a
``user_request`` (ask_user / terminal / page approval) fires on a session
that belongs to a Telegram binding, the prompt is forwarded to the bound
chat with Approve/Deny (confirm), the offered choices (choice), or a
"answer in the Nexus UI" note (text/form — free-form input can't ride
inline buttons).

Button presses resolve through the router (``store.resolve_pending`` —
the same primitive ``POST /chat/{sid}/respond`` uses), so a Telegram
answer and a UI answer race exactly like two browser tabs.
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

        keyboard, no_buttons_reason = self._router.build_hitl_keyboard(
            session_id, request_id, data
        )
        if keyboard is None:
            lines.append(f"\nℹ️ This needs free-form input — {no_buttons_reason}.")

        reply_markup = {"inline_keyboard": keyboard} if keyboard else None
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
        log.info(
            "telegram: forwarded HITL %s request %s (kind=%s, buttons=%s)",
            request_id[:8],
            session_id[:8],
            kind,
            bool(keyboard),
        )

    async def _cancelled(self, data: dict) -> None:
        request_id = str(data.get("request_id") or "")
        fwd = None
        router = self._router
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
