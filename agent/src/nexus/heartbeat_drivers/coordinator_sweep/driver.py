"""Coordinator sweep heartbeat driver.

When ``[coordinator]`` is enabled and ``sweep_interval_minutes`` has
elapsed (outside quiet hours, with no active turn on the master session),
runs one read-only sweep turn in the coordinator session and delivers the
resulting digest to the Telegram chat bound to the master session (the
owner's DM). The scheduler ticks every 30 minutes; interval gating is
done here because the markdown schedule is static.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from loom.heartbeat import HeartbeatDriver, HeartbeatEvent

log = logging.getLogger(__name__)

_SWEEP_PROMPT = """\
SWEEP (read-only): run a proactive check across Nexus now.

1. Use nexus_sessions (action='projects') for every project's state.
2. Read the tails of the most recently updated chats that matter.
3. Note anything upcoming (calendar events soon), stale, or unfinished.

Then output a concise digest in Markdown:
- One line per project with anything new/changed/needed.
- A short "Needs you" list with at most 3 items that genuinely need the \
user (decisions, blocked work, forgotten threads).
- Keep it under 150 words. If nothing meaningful changed since the last \
sweep, output exactly: NOTHING_NEW.

Rules: this is a read-only sweep — no session_dispatch, no writes, no \
messages. Just observe and report."""


def _tg_config():
    from nexus.config_file import load_cached

    return load_cached()


class Driver(HeartbeatDriver):
    async def check(self, state: dict[str, Any]) -> tuple[list[HeartbeatEvent], dict[str, Any]]:
        try:
            cfg = _tg_config()
        except Exception:
            log.exception("coordinator_sweep: config load failed")
            return [], state

        coord_cfg = getattr(cfg, "coordinator", None)
        if coord_cfg is None or not coord_cfg.enabled:
            return [], state
        interval = int(getattr(coord_cfg, "sweep_interval_minutes", 0) or 0)
        if interval <= 0:
            return [], state

        from nexus.coordinator import get_service, in_quiet_hours, sweep_mode

        service = get_service()
        if service is None:
            return [], state

        now = datetime.now(UTC)
        last_iso = state.get("last_sweep")
        if last_iso:
            try:
                elapsed = now - datetime.fromisoformat(last_iso)
                if elapsed < timedelta(minutes=interval):
                    return [], state
            except ValueError:
                pass

        if in_quiet_hours(coord_cfg.quiet_hours):
            log.debug("coordinator_sweep: quiet hours, skipping")
            return [], state

        sid = service.session_id
        if sid is None or service.store.get(sid) is None:
            return [], state

        from nexus.server.services.chat_turn_runner import get_running_turn

        if get_running_turn(sid) is not None:
            log.debug("coordinator_sweep: master chat busy, skipping")
            return [], state

        state["last_sweep"] = now.isoformat()
        log.info("coordinator_sweep: running sweep on %s", sid)

        from nexus.server.services.background_turn import run_background_turn

        try:
            with sweep_mode():
                result = await run_background_turn(
                    session_id=sid,
                    seed_message=_SWEEP_PROMPT,
                    agent_=service.agent,
                    store=service.store,
                )
        except Exception:
            log.exception("coordinator_sweep: sweep turn failed")
            return [], state

        digest = (result.accumulated_text or "").strip()
        if not digest or digest.strip().strip("*`#").upper().startswith("NOTHING_NEW"):
            log.debug("coordinator_sweep: nothing new, no delivery")
            return [], state

        await self._deliver(cfg, sid, digest)
        return [], state

    async def _deliver(self, cfg: Any, sid: str, digest: str) -> None:
        """Send the digest to the Telegram chat bound to the master session.

        Outbound-only: a short-lived API client that never calls getUpdates,
        so it cannot conflict with the running poller's long-poll.
        """
        try:
            from nexus.telegram.api import TelegramClient
            from nexus.telegram.bindings import TelegramBindingStore
            from nexus.telegram.formatting import md_to_telegram_html

            binding = TelegramBindingStore().find_by_session(sid)
            if binding is None:
                log.debug("coordinator_sweep: no telegram binding for master chat")
                return
            tg_cfg = cfg.telegram
            if not tg_cfg.enabled:
                return
            client = TelegramClient.from_config(tg_cfg)
            if client is None:
                return
            try:
                text = md_to_telegram_html(digest)
                # Split conservatively (Telegram hard limit is 4096 UTF-16 units).
                for i in range(0, len(text), 3500):
                    await client.send_text_safe(
                        binding.chat_id,
                        text[i : i + 3500],
                        thread_id=binding.thread_id or None,
                    )
            finally:
                await client.aclose()
        except Exception:
            log.exception("coordinator_sweep: digest delivery failed")
