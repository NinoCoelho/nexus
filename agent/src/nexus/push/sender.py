"""Web Push fan-out for HITL prompts.

Fired from ``PubSubMixin`` whenever a ``user_request`` event publishes.
Reads the live subscription list from the session store, sends one
encrypted push per subscription via pywebpush (run in a thread pool so
the asyncio loop isn't blocked), and prunes endpoints that respond
410/404 (subscription expired or unsubscribed).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from .keys import load_or_create

if TYPE_CHECKING:
    from ..server.session_store import SessionStore

log = logging.getLogger(__name__)


def is_configured() -> bool:
    """Cheap check before scheduling a push task — keys load lazily."""
    try:
        load_or_create()
        return True
    except Exception:  # noqa: BLE001
        log.exception("VAPID key load failed — push disabled")
        return False


def get_public_key() -> str:
    return load_or_create().public_key


async def fan_out(
    *,
    store: "SessionStore",
    session_id: str,
    request_data: dict[str, Any],
) -> None:
    """Push the HITL prompt to every registered subscription."""
    keys = load_or_create()
    subs = store.list_push_subscriptions()
    if not subs:
        return

    title = "Nexus needs input"
    body = (request_data.get("prompt") or "").strip()
    if len(body) > 240:
        body = body[:237] + "…"
    payload = json.dumps({
        "title": title,
        "body": body,
        "session_id": session_id,
        "request_id": request_data.get("request_id"),
        "kind": request_data.get("kind"),
        "timeout_seconds": request_data.get("timeout_seconds"),
    }, ensure_ascii=False)

    # pywebpush is sync (urllib3 under the hood). Run each delivery in
    # the default thread pool so the publish path stays non-blocking.
    await asyncio.gather(
        *[_deliver_one(store, sub, payload, keys) for sub in subs],
        return_exceptions=True,
    )


async def _deliver_one(
    store: "SessionStore",
    sub: dict[str, Any],
    payload: str,
    keys: Any,
) -> None:
    endpoint = sub["endpoint"]
    try:
        await asyncio.to_thread(
            _send_sync,
            endpoint=endpoint,
            p256dh=sub["p256dh"],
            auth=sub["auth"],
            payload=payload,
            vapid_private_key=keys.private_key,
            vapid_subject=keys.subject,
        )
    except _GoneError:
        # 404/410 — subscription is dead. Drop it so we don't keep
        # paying postage on a returned envelope.
        log.info("pruning expired push subscription: %s", endpoint)
        store.delete_push_subscription(endpoint)
    except Exception:  # noqa: BLE001
        log.warning("push delivery failed for %s", endpoint, exc_info=True)


class _GoneError(Exception):
    """Raised when a push endpoint returns 404/410 — sub should be deleted."""


def _send_sync(
    *,
    endpoint: str,
    p256dh: str,
    auth: str,
    payload: str,
    vapid_private_key: str,
    vapid_subject: str,
) -> None:
    """Blocking pywebpush call — invoked via asyncio.to_thread."""
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info={
                "endpoint": endpoint,
                "keys": {"p256dh": p256dh, "auth": auth},
            },
            data=payload,
            vapid_private_key=vapid_private_key,
            vapid_claims={"sub": vapid_subject},
            ttl=300,
        )
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (404, 410):
            raise _GoneError() from exc
        raise
