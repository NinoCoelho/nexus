"""Dream trigger heartbeat driver — checks schedule and dispatches dream runs."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from loom.heartbeat import HeartbeatDriver, HeartbeatEvent

log = logging.getLogger(__name__)

_MIN_SESSIONS = 5
_MIN_INTERVAL_HOURS = 24


class Driver(HeartbeatDriver):
    async def check(
        self, state: dict[str, Any]
    ) -> tuple[list[HeartbeatEvent], dict[str, Any]]:
        try:
            from nexus.config_file import load
            cfg = load()
        except Exception:
            log.exception("dream_trigger: config load failed")
            return [], state

        dream_cfg = getattr(cfg, "dream", None)
        if dream_cfg is None or not dream_cfg.enabled:
            return [], state

        try:
            from nexus.dream.state import DreamStateStore
            from pathlib import Path
            store = DreamStateStore(Path.home() / ".nexus" / "dream_state.sqlite")
        except Exception:
            log.exception("dream_trigger: state store init failed")
            return [], state

        if store.is_running():
            log.debug("dream_trigger: dream already running, skipping")
            store.close()
            return [], state

        last_run = store.last_run()
        now = datetime.now(UTC)

        if last_run and last_run.finished_at:
            elapsed = now - last_run.finished_at
            if elapsed < timedelta(hours=_MIN_INTERVAL_HOURS):
                log.debug(
                    "dream_trigger: last dream was %.1fh ago, need >= %dh",
                    elapsed.total_seconds() / 3600, _MIN_INTERVAL_HOURS,
                )
                store.close()
                return [], state

        try:
            min_sessions = dream_cfg.min_sessions_since_last or _MIN_SESSIONS
            recent_count = _count_recent_sessions(
                since=last_run.started_at if last_run else None,
            )
            if last_run and recent_count < min_sessions:
                log.debug(
                    "dream_trigger: only %d sessions since last dream, need >= %d",
                    recent_count, min_sessions,
                )
                store.close()
                return [], state
        except Exception:
            log.exception("dream_trigger: session count check failed")
            store.close()
            return [], state

        depth = _pick_depth(last_run, now)

        try:
            from nexus.dream.engine import run_dream
            provider, upstream_model = _get_provider()
            if provider is None:
                log.warning("dream_trigger: no provider available")
                store.close()
                return [], state

            log.info(
                "dream_trigger: triggering %s dream (last=%s, sessions=%d)",
                depth,
                last_run.started_at.isoformat() if last_run else "never",
                recent_count,
            )

            result = await run_dream(
                provider=provider,
                model_id=upstream_model,
                cfg=cfg,
                depth=depth,
            )

            log.info(
                "dream_trigger: dream run #%d completed (status=%s)",
                result.run_id, "error" if result.error else "ok",
            )
        except Exception:
            log.exception("dream_trigger: dream run failed")

        store.close()
        return [], state


def _count_recent_sessions(since: datetime | None) -> int:
    try:
        from pathlib import Path
        import sqlite3
        db_path = Path.home() / ".nexus" / "sessions.sqlite"
        if not db_path.exists():
            return 0
        conn = sqlite3.connect(str(db_path))
        try:
            if since:
                since_iso = since.strftime("%Y-%m-%dT%H:%M:%S")
                row = conn.execute(
                    "SELECT COUNT(*) FROM sessions WHERE updated_at > ?",
                    (since_iso,),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except Exception:
        log.exception("dream_trigger: session count query failed")
        return 0


def _pick_depth(last_run: Any, now: datetime) -> str:
    if last_run is None:
        return "medium"
    if last_run.finished_at is None:
        return "light"
    elapsed_days = (now - last_run.finished_at).total_seconds() / 86400
    if elapsed_days >= 7:
        return "deep"
    if elapsed_days >= 1:
        return "medium"
    return "light"


def _get_provider() -> tuple[Any, str | None]:
    try:
        from nexus.agent.llm import OpenAIProvider, StaticBearerAuth
        from nexus.agent.registry import build_registry
        from nexus.config_file import load
        from nexus.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

        cfg = load()
        registry = build_registry(cfg)

        try:
            provider, upstream = registry.get_for_model(cfg.agent.default_model)
            return provider, upstream
        except KeyError:
            available = registry.available_model_ids()
            if available:
                provider, upstream = registry.get_for_model(available[0])
                return provider, upstream

        return OpenAIProvider(
            base_url=LLM_BASE_URL,
            auth=StaticBearerAuth(LLM_API_KEY),
            model=LLM_MODEL,
        ), None
    except Exception:
        log.exception("dream_trigger: provider init failed")
        return None, None
