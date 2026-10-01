"""Thin Telegram Bot API client (outbound-only, httpx).

Deliberately minimal — just what the gateway needs: long-poll updates,
send/edit text messages (with inline keyboards), typing action, callback
answers. 429 rate limits are retried transparently with the server-provided
``retry_after``; anything else raises ``TelegramError`` with a readable
message.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from ..secrets import resolve

log = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"
# Hard Telegram limit is 4096 chars per message; leave headroom for the
# "… (1/2)" suffix the splitter appends.
MAX_MESSAGE_LEN = 4000


class TelegramError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class TelegramConflictError(TelegramError):
    """Another getUpdates consumer is polling the same token (HTTP 409)."""


class TelegramClient:
    def __init__(
        self,
        token: str,
        *,
        proxy_url: str = "",
        timeout: float = 35.0,
    ) -> None:
        self._token = token
        proxy = proxy_url or None
        self._http = httpx.AsyncClient(
            base_url=f"{_API_BASE}/bot{token}",
            timeout=httpx.Timeout(timeout, connect=10.0),
            proxy=proxy,
            # Follow Bot API redirects (rare, but e.g. file downloads may).
            follow_redirects=True,
        )
        # File downloads live under /file/bot<token>/, not the method path.
        self._file_base = f"{_API_BASE}/file/bot{token}"
        self._closed = False

    # ── Construction helpers ─────────────────────────────────────────────

    @classmethod
    def from_config(cls, cfg: Any) -> "TelegramClient | None":
        """Build a client from the ``[telegram]`` config section.

        Returns None (and logs why) when the token isn't configured —
        the poller treats that as "disabled".
        """
        token = resolve(cfg.bot_token_env)
        if not token:
            log.info(
                "telegram: bot token not found (env/secrets key %r) — poller not started",
                cfg.bot_token_env,
            )
            return None
        return cls(
            token,
            proxy_url=cfg.proxy_url,
            timeout=max(35.0, cfg.poll_timeout_seconds + 10.0),
        )

    @property
    def available(self) -> bool:
        return not self._closed and bool(self._token)

    async def aclose(self) -> None:
        self._closed = True
        await self._http.aclose()

    # ── Core request ─────────────────────────────────────────────────────

    async def _call(
        self,
        method: str,
        payload: dict[str, Any],
        *,
        retries: int = 2,
        files: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            if files is not None:
                # Multipart upload (sendVoice/sendAudio): form fields ride
                # as `data`, the media as `files`.
                resp = await self._http.post(f"/{method}", data=payload, files=files)
            else:
                resp = await self._http.post(f"/{method}", json=payload)
        except httpx.HTTPError as exc:
            if retries > 0:
                await asyncio.sleep(1.5)
                return await self._call(method, payload, retries=retries - 1, files=files)
            raise TelegramError(f"network error calling {method}: {exc}") from exc

        if resp.status_code == 429:
            retry_after = 1.5
            try:
                retry_after = float(resp.json().get("parameters", {}).get("retry_after", 1.5))
            except Exception:  # noqa: BLE001 — malformed body, use default
                pass
            if retries > 0:
                await asyncio.sleep(retry_after + 0.25)
                return await self._call(method, payload, retries=retries - 1)
            raise TelegramError(f"rate limited on {method} after retries")

        if resp.status_code == 409:
            raise TelegramConflictError(
                "409 Conflict from Telegram — another process is polling getUpdates "
                "with this bot token (e.g. the daemon and a foreground server are "
                "both running). Stop one of them."
            )

        if resp.status_code >= 400:
            desc = ""
            try:
                desc = resp.json().get("description", "")
            except Exception:  # noqa: BLE001
                pass
            raise TelegramError(
                f"{method} failed ({resp.status_code}): {desc or resp.text[:200]}",
                status_code=resp.status_code,
            )

        body = resp.json()
        if not body.get("ok"):
            raise TelegramError(f"{method} returned ok=false: {body.get('description')}")
        return body.get("result", {})

    # ── Updates ──────────────────────────────────────────────────────────

    async def get_updates(
        self, *, offset: int, timeout_seconds: int
    ) -> list[dict[str, Any]]:
        # httpx-wide timeout was sized for the poll in from_config; guard the
        # per-request read so a short client timeout doesn't abort long polls.
        result = await self._call(
            "getUpdates",
            {
                "offset": offset,
                "timeout": timeout_seconds,
                "allowed_updates": ["message", "callback_query", "edited_message"],
            },
        )
        return result if isinstance(result, list) else []

    # ── Messages ─────────────────────────────────────────────────────────

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        thread_id: int | None = None,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = "HTML",
    ) -> int:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if thread_id:
            payload["message_thread_id"] = thread_id
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        msg = await self._call("sendMessage", payload)
        return int(msg.get("message_id", 0))

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = "HTML",
    ) -> bool:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            await self._call("editMessageText", payload)
            return True
        except TelegramError as exc:
            # "message is not modified" is benign during streaming edits.
            if "message is not modified" in str(exc):
                return True
            # "message to edit not found" — original was deleted; let caller
            # fall back to sending a fresh message.
            if "message to edit not found" in str(exc):
                return False
            raise

    async def send_chat_action(
        self, chat_id: int, action: str = "typing", *, thread_id: int | None = None
    ) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "action": action}
        if thread_id:
            payload["message_thread_id"] = thread_id
        try:
            await self._call("sendChatAction", payload)
        except TelegramError:
            # Typing indicators are best-effort.
            log.debug("telegram: sendChatAction failed", exc_info=True)

    async def answer_callback_query(
        self, callback_query_id: str, text: str = ""
    ) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text[:190]
        try:
            await self._call("answerCallbackQuery", payload)
        except TelegramError:
            log.debug("telegram: answerCallbackQuery failed", exc_info=True)

    async def get_me(self) -> dict[str, Any]:
        return await self._call("getMe", {})

    # ── Files ────────────────────────────────────────────────────────────

    async def get_file(self, file_id: str) -> dict[str, Any]:
        """Resolve a file_id to ``{file_id, file_size, file_path}``.

        Bot-API downloads are capped at 20 MB by Telegram — check
        ``file_size`` before downloading.
        """
        return await self._call("getFile", {"file_id": file_id})

    async def download_file(self, file_path: str) -> bytes:
        url = f"{self._file_base}/{file_path.lstrip('/')}"
        try:
            resp = await self._http.get(url)
        except httpx.HTTPError as exc:
            raise TelegramError(f"file download failed: {exc}") from exc
        if resp.status_code != 200:
            raise TelegramError(
                f"file download failed ({resp.status_code})", status_code=resp.status_code
            )
        return resp.content

    # ── Voice / audio replies ────────────────────────────────────────────

    async def send_voice(
        self,
        chat_id: int,
        audio: bytes,
        filename: str,
        *,
        thread_id: int | None = None,
        caption: str = "",
    ) -> int:
        """Send a voice note (OGG/Opus required by Telegram)."""
        payload: dict[str, Any] = {"chat_id": chat_id}
        if thread_id:
            payload["message_thread_id"] = thread_id
        if caption:
            payload["caption"] = caption[:900]
        msg = await self._call(
            "sendVoice",
            payload,
            files={"voice": (filename, audio, "audio/ogg")},
        )
        return int(msg.get("message_id", 0))

    async def send_audio(
        self,
        chat_id: int,
        audio: bytes,
        filename: str,
        mime: str,
        *,
        thread_id: int | None = None,
    ) -> int:
        """Send an audio file (music-player bubble). Fallback for sendVoice
        when the payload isn't OGG/Opus."""
        payload: dict[str, Any] = {"chat_id": chat_id}
        if thread_id:
            payload["message_thread_id"] = thread_id
        msg = await self._call(
            "sendAudio",
            payload,
            files={"audio": (filename, audio, mime)},
        )
        return int(msg.get("message_id", 0))

    async def set_message_reaction(
        self, chat_id: int, message_id: int, emoji: str = ""
    ) -> None:
        """Set (or, with an empty emoji, remove) the bot's reaction on a message.

        Payload note: the Bot API takes ``reaction`` as a JSON-serialized
        list of ReactionType — NOT an ``emoji`` string (an unknown param is
        silently ignored and a missing ``reaction`` *clears* reactions,
        which made acks succeed invisibly).

        Best-effort: Telegram only accepts its fixed reaction-emoji set for
        bots and reactions can race message deletion — failures never
        propagate to the caller. They ARE logged at warning (with the API's
        own description) because a silently-missing ack is indistinguishable
        from a broken flow.
        """
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "reaction": [{"type": "emoji", "emoji": emoji}] if emoji else [],
        }
        try:
            await self._call("setMessageReaction", payload)
        except TelegramError as exc:
            log.warning(
                "telegram: setMessageReaction(chat=%s msg=%s emoji=%r) failed: %s",
                chat_id, message_id, emoji, exc,
            )

    # ── HTML-safe wrappers ───────────────────────────────────────────────

    async def send_text_safe(
        self,
        chat_id: int,
        text: str,
        *,
        thread_id: int | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> int:
        """Send text as HTML, falling back to raw text if parsing fails."""
        try:
            return await self.send_message(
                chat_id, text, thread_id=thread_id, reply_markup=reply_markup
            )
        except TelegramError as exc:
            if "can't parse" in str(exc).lower():
                return await self.send_message(
                    chat_id,
                    text,
                    thread_id=thread_id,
                    reply_markup=reply_markup,
                    parse_mode=None,
                )
            raise

    async def edit_text_safe(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        """Edit as HTML, falling back to raw text if parsing fails."""
        try:
            return await self.edit_message_text(
                chat_id, message_id, text, reply_markup=reply_markup
            )
        except TelegramError as exc:
            if "can't parse" in str(exc).lower():
                return await self.edit_message_text(
                    chat_id,
                    message_id,
                    text,
                    reply_markup=reply_markup,
                    parse_mode=None,
                )
            raise
