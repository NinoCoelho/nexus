"""Telegram bot management routes: status + live start/stop.

The [telegram] config section itself is edited through the generic
``PATCH /config`` (see routes/config.py). These routes cover the runtime:
``GET /telegram/status`` reflects poller state + token presence + the
bot identity; ``POST /telegram/start|stop`` (re)start or stop the poller
without a server restart — start validates the bot token synchronously
via getMe so the UI gets an immediate "connected as @bot" or a 400 with
the Telegram error (bad token, unreachable, 409 conflict).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ...config_file import load_cached as load_config
from ...secrets import resolve as secrets_resolve

log = logging.getLogger(__name__)

router = APIRouter()


def _status(app: Any) -> dict[str, Any]:
    cfg = load_config().telegram
    poller = getattr(app.state, "telegram_poller", None)
    running = bool(poller is not None and poller.running)
    bot = getattr(poller, "bot_info", None) or {}
    return {
        "enabled": cfg.enabled,
        "running": running,
        "has_token": bool(secrets_resolve(cfg.bot_token_env)),
        "token_env": cfg.bot_token_env,
        "bot_username": bot.get("username"),
        "allowed_user_ids": cfg.allowed_user_ids,
        "allowlist_empty": not cfg.allowed_user_ids,
        # Why the poller isn't delivering, when it's alive but failing
        # (e.g. 401 bad token) or died (last error before exit).
        "error": getattr(poller, "last_error", None),
    }


@router.get("/telegram/status")
async def telegram_status(request: Request) -> dict[str, Any]:
    return _status(request.app)


@router.post("/telegram/start")
async def telegram_start(request: Request) -> dict[str, Any]:
    from ..events import SessionEvent
    from ...telegram.api import TelegramError
    from ...telegram.poller import build_telegram_poller

    app = request.app

    old = getattr(app.state, "telegram_poller", None)
    if old is not None:
        try:
            await old.stop()
        except Exception:
            log.exception("telegram: stopping old poller failed")
        app.state.telegram_poller = None

    cfg = load_config().telegram
    poller = build_telegram_poller(
        cfg=cfg,
        agent=app.state.agent,
        store=app.state.sessions,
        tracker=app.state.job_tracker,
        publish_job_event=lambda kind, data: app.state.sessions.publish(
            "__jobs__", SessionEvent(kind=kind, data=data)
        ),
    )
    if poller is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"No bot token configured under {cfg.bot_token_env!r} — "
                "paste the token from @BotFather first."
            ),
        )

    # Validate the token synchronously so the UI gets an immediate verdict.
    try:
        me = await poller._client.get_me()
        poller.bot_info = me
    except TelegramError as exc:
        await poller._client.aclose()
        raise HTTPException(
            status_code=400,
            detail=f"Telegram rejected the connection: {exc}",
        ) from exc
    except Exception as exc:  # network hiccup — start anyway, poller retries
        log.warning("telegram: getMe validation failed (%s); starting anyway", exc)

    poller.start()
    app.state.telegram_poller = poller
    log.info("telegram poller started via API as @%s", poller.bot_info.get("username"))
    return _status(app)


@router.post("/telegram/stop")
async def telegram_stop(request: Request) -> dict[str, Any]:
    app = request.app
    poller = getattr(app.state, "telegram_poller", None)
    if poller is not None:
        try:
            await poller.stop()
        except Exception:
            log.exception("telegram: poller stop failed")
        app.state.telegram_poller = None
    return _status(app)
