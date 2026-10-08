"""Coordinator sweep heartbeat driver.

When ``[coordinator]`` is enabled and ``sweep_interval_minutes`` has
elapsed (outside quiet hours, with no active turn on the master session),
runs one read-only sweep turn in the coordinator session and delivers the
resulting digest to the Telegram chat bound to the master session (the
owner's DM). The scheduler ticks every 30 minutes; interval gating is
done here because the markdown schedule is static.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from loom.heartbeat import HeartbeatDriver, HeartbeatEvent

log = logging.getLogger(__name__)

# Single-flight guard for the detached sweep task. check() returns
# immediately (a sweep is a full LLM turn — running it inline would block
# the scheduler's tick for minutes); this flag plus the last_sweep gate in
# state prevents overlapping sweeps.
_sweep_in_flight = False

_SWEEP_PROMPT = """\
SWEEP (read-only): run a proactive check across Nexus now.

1. Use nexus_sessions (action='projects') for every project's state.
2. Read the tails of the most recently updated chats that matter.
3. Note anything upcoming (calendar events soon), stale, or unfinished.

Then output a concise digest in Markdown:
- One line per project with anything new/changed/needed.
- A short "Needs you" list with at most 3 items that genuinely need the \
user (decisions, blocked work, forgotten threads).
- Keep it under 150 words. If nothing meaningful changed, output exactly: \
NOTHING_NEW.

Rules: this is a read-only sweep — no session_dispatch, no writes, no \
messages. Just observe and report. Each sweep starts with a clean context, \
so the previous digest below is your only memory of what you already told \
the user; everything else you need, read with your tools."""

_PREVIOUS_DIGEST_TEMPLATE = """

--- Previous digest (what you already reported; do not repeat it) ---
{digest}
--- end previous digest ---"""


def _build_seed() -> str:
    """Sweep prompt plus the previous digest, which is the sweep's only memory.

    Sweeps run ephemerally (no session history), so continuity comes from the
    vault log rather than from an ever-growing transcript.
    """
    from nexus.coordinator_sweeps import load_last_digest

    try:
        previous = load_last_digest()
    except Exception:  # noqa: BLE001 — a sweep without memory beats no sweep
        log.debug("coordinator_sweep: could not load previous digest", exc_info=True)
        previous = None
    if not previous:
        return _SWEEP_PROMPT
    return _SWEEP_PROMPT + _PREVIOUS_DIGEST_TEMPLATE.format(digest=previous.strip())


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

        global _sweep_in_flight
        if _sweep_in_flight:
            log.debug("coordinator_sweep: previous sweep still running, skipping")
            return [], state

        state["last_sweep"] = now.isoformat()
        log.info("coordinator_sweep: dispatching sweep on %s (detached)", sid)

        async def _run_sweep() -> None:
            global _sweep_in_flight
            _sweep_in_flight = True
            try:
                from nexus.coordinator_sweeps import append_digest
                from nexus.server.services.background_turn import run_background_turn

                try:
                    with sweep_mode():
                        result = await run_background_turn(
                            session_id=sid,
                            seed_message=_build_seed(),
                            # The restricted read-only registry: a sweep needs
                            # inspection and vault reads, not the full ~15K
                            # tokens of tool schemas.
                            agent_=service.sweep_agent,
                            store=service.store,
                            model_id=getattr(coord_cfg, "sweep_model", "") or None,
                            # Fresh context, nothing written back. Persisting
                            # sweeps made each one re-send every previous
                            # sweep's prompt, tool calls and tool results.
                            history_override=[],
                            ephemeral=True,
                        )
                except Exception:
                    log.exception("coordinator_sweep: sweep turn failed")
                    return

                usage = result.usage or {}
                log.info(
                    "coordinator_sweep: finished status=%s tokens=%s in / %s out "
                    "(cache %s read / %s write) iterations=%s model=%s",
                    result.status,
                    usage.get("input_tokens", 0),
                    usage.get("output_tokens", 0),
                    usage.get("cache_read_tokens", 0),
                    usage.get("cache_write_tokens", 0),
                    usage.get("iterations", 0),
                    usage.get("model", ""),
                )

                # The final assistant message, not every streamed delta —
                # accumulated_text includes intermediate narration from each
                # tool iteration, which both polluted the digest and broke
                # the NOTHING_NEW check below.
                digest = result.final_reply()
                if not digest or digest.strip().strip("*`#").upper().startswith("NOTHING_NEW"):
                    log.debug("coordinator_sweep: nothing new, no delivery")
                    return

                append_digest(
                    digest,
                    tokens_in=int(usage.get("input_tokens") or 0),
                    tokens_out=int(usage.get("output_tokens") or 0),
                    iterations=int(usage.get("iterations") or 0),
                    model=str(usage.get("model") or ""),
                )
                await self._deliver(cfg, sid, digest)
            finally:
                _sweep_in_flight = False

        # Detached: the scheduler tick must never wait on an LLM turn.
        asyncio.create_task(_run_sweep(), name="coordinator-sweep")
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
