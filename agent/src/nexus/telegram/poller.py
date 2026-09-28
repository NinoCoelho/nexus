"""TelegramPoller — owns the getUpdates long-poll loop.

Modeled on ``broker/poller.py``: an asyncio task + stop event, started
and stopped from the server lifespan. Updates are dispatched through the
``TelegramRouter`` with per-(chat, thread) serialization — ordering within
one conversation is preserved (a /switch then a message must apply in
order), while different chats/topics run concurrently.

Config is re-read every cycle so allowlist edits apply without a restart.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .api import TelegramClient, TelegramConflictError, TelegramError
from .bindings import TelegramBindingStore
from .hitl import HitlForwarder
from .router import TelegramRouter

log = logging.getLogger(__name__)


class TelegramPoller:
    def __init__(
        self,
        *,
        client: TelegramClient,
        agent: Any,
        store: Any,
        tracker: Any,
        cfg: Any,
        publish_job_event: Any = None,
        bindings: TelegramBindingStore | None = None,
    ) -> None:
        self._client = client
        self._store = store
        self._cfg = cfg
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._chains: dict[tuple[int, int], asyncio.Task] = {}
        # getMe() result cached at poll start — surfaced by GET /telegram/status.
        self.bot_info: dict[str, Any] = {}
        # Last poll-cycle error (e.g. 401 bad token). Surfaced via
        # GET /telegram/status so the UI can explain a dead poller.
        self.last_error: str | None = None
        self.router = TelegramRouter(
            client=client,
            agent=agent,
            store=store,
            tracker=tracker,
            bindings=bindings or TelegramBindingStore(),
            cfg=cfg,
            publish_job_event=publish_job_event,
        )
        self._hitl = HitlForwarder(self.router)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.get_running_loop().create_task(self._run())
        self._hitl.start()
        log.info("telegram: poller started")

    async def stop(self) -> None:
        self._stop_event.set()
        await self._hitl.stop()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        for task in self._chains.values():
            if not task.done():
                task.cancel()
        self._chains.clear()
        await self.router.aclose()
        await self._client.aclose()
        log.info("telegram: poller stopped")

    async def _run(self) -> None:
        offset = 0

        # Startup diagnostics: verify the token and log the bot identity.
        try:
            me = await self._client.get_me()
            self.bot_info = me
            log.info(
                "telegram: polling as @%s (id=%s)",
                me.get("username", "?"),
                me.get("id", "?"),
            )
        except TelegramError as exc:
            if getattr(exc, "status_code", None) == 401:
                log.error(
                    "telegram: bot token rejected (401) — check %s. Poller disabled.",
                    self._cfg.bot_token_env,
                )
                return
            log.warning("telegram: getMe failed (%s); polling anyway", exc)

        while not self._stop_event.is_set():
            try:
                updates = await self._client.get_updates(
                    offset=offset, timeout_seconds=self._cfg.poll_timeout_seconds
                )
                self.last_error = None
                for update in updates:
                    # Acknowledge immediately — Telegram redelivers unconfirmed
                    # updates after the long-poll timeout.
                    offset = max(offset, int(update.get("update_id", 0)) + 1)
                    self._dispatch(update)
            except TelegramConflictError as exc:
                self.last_error = str(exc)
                log.error("telegram: %s — sleeping 30s", exc)
                await self._sleep_or_stop(30.0)
            except TelegramError as exc:
                # A rejected token never recovers by retrying — disable the
                # poller (status surfaces the error; re-save the token and
                # press Start, which rebuilds the client).
                if getattr(exc, "status_code", None) == 401:
                    self.last_error = (
                        "Telegram rejected the bot token (401). Re-save the "
                        "token in Settings → Features → Telegram and press "
                        "Start again."
                    )
                    log.error("telegram: %s", self.last_error)
                    return
                self.last_error = str(exc)
                log.exception("telegram: poll cycle failed")
                await self._sleep_or_stop(5.0)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.last_error = "unexpected poller error"
                log.exception("telegram: poller loop error")
                await self._sleep_or_stop(5.0)

            # Hot-reload config (allowlist edits, proxy won't apply until
            # restart — client is already constructed).
            try:
                from ..config_file import load_cached as load_config

                self._cfg = load_config().telegram
                self.router.cfg = self._cfg
                self.router.deps.cfg = self._cfg
            except Exception:
                pass

    async def _sleep_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    # ── Per-conversation serialized dispatch ─────────────────────────────

    def _update_key(self, update: dict) -> tuple[int, int]:
        payload = update.get("message") or update.get("callback_query", {})
        msg = payload.get("message") or payload
        chat = msg.get("chat", {})
        chat_id = int(chat.get("id", 0))
        thread_id = int(msg.get("message_thread_id") or 0)
        return chat_id, thread_id

    def _dispatch(self, update: dict) -> None:
        key = self._update_key(update)
        prev = self._chains.get(key)

        async def _run_after_prev() -> None:
            if prev is not None and not prev.done():
                try:
                    await prev
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass  # prior handler's failure must not block this one
            await self.router.handle_update(update)

        task = asyncio.create_task(
            _run_after_prev(), name=f"telegram-update-{key[0]}-{key[1]}"
        )
        self._chains[key] = task
        # Opportunistic cleanup of finished chain tails.
        if len(self._chains) > 64:
            self._chains = {
                k: t for k, t in self._chains.items() if not t.done()
            }


def build_telegram_poller(
    *,
    cfg: Any,
    agent: Any,
    store: Any,
    tracker: Any,
    publish_job_event: Any = None,
) -> TelegramPoller | None:
    """Build a poller from a ``[telegram]`` config section.

    Returns None when the bot token isn't resolvable (env var or
    ``~/.nexus/secrets.toml``). Shared by the lifespan startup and the
    ``POST /telegram/start`` route.
    """
    from .api import TelegramClient

    client = TelegramClient.from_config(cfg)
    if client is None:
        return None
    if publish_job_event is None:
        from ..server.events import SessionEvent

        def publish_job_event(kind: str, data: dict) -> None:  # noqa: F811
            store.publish("__jobs__", SessionEvent(kind=kind, data=data))

    return TelegramPoller(
        client=client,
        agent=agent,
        store=store,
        tracker=tracker,
        cfg=cfg,
        publish_job_event=publish_job_event,
    )
