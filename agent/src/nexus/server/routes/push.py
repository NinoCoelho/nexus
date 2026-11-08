"""Push subscription management (Web Push + APNs).

Browser service-worker endpoints:

* ``GET /push/vapid-public-key`` — public key the SW needs to call
  ``pushManager.subscribe``.
* ``POST /push/subscribe`` — upserts the browser-issued subscription
  (endpoint + p256dh + auth) so the backend can encrypt push payloads
  to it later.
* ``DELETE /push/subscribe`` — removes a subscription (called from the
  SW on ``pushsubscriptionchange`` or when permission is revoked).

Native iOS app (APNs):

* ``POST /push/apns/register`` — upserts the Apple-issued device token.
  The app re-registers on every launch (tokens rotate); delivery only
  happens when ``[push].apns`` is fully configured server-side.
* ``DELETE /push/apns/register`` — removes the device token (logout /
  notifications off).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..deps import get_sessions
from ..session_store import SessionStore

log = logging.getLogger(__name__)

router = APIRouter(prefix="/push")


class _SubKeys(BaseModel):
    p256dh: str
    auth: str


class SubscribePayload(BaseModel):
    endpoint: str = Field(min_length=10)
    keys: _SubKeys


class UnsubscribePayload(BaseModel):
    endpoint: str = Field(min_length=10)


class ApnsRegisterPayload(BaseModel):
    # 64-hex (or longer, future-proof) Apple device token from
    # UIApplication didRegisterForRemoteNotifications / modern equivalent.
    token: str = Field(min_length=32)
    bundle_id: str | None = None
    environment: str = Field(default="production", pattern="^(production|sandbox)$")


class ApnsUnregisterPayload(BaseModel):
    token: str = Field(min_length=32)


@router.get("/vapid-public-key")
def vapid_public_key() -> dict[str, str]:
    from ...push import sender as push_sender
    return {"public_key": push_sender.get_public_key()}


@router.post("/subscribe")
def subscribe(
    payload: SubscribePayload,
    request: Request,
    store: SessionStore = Depends(get_sessions),
) -> dict[str, bool]:
    user_agent = request.headers.get("user-agent")
    store.upsert_push_subscription(
        endpoint=payload.endpoint,
        p256dh=payload.keys.p256dh,
        auth=payload.keys.auth,
        user_agent=user_agent,
    )
    return {"ok": True}


@router.delete("/subscribe")
def unsubscribe(
    payload: UnsubscribePayload,
    store: SessionStore = Depends(get_sessions),
) -> dict[str, bool]:
    removed = store.delete_push_subscription(payload.endpoint)
    if not removed:
        raise HTTPException(status_code=404, detail="subscription not found")
    return {"ok": True}


@router.post("/apns/register")
def apns_register(
    payload: ApnsRegisterPayload,
    store: SessionStore = Depends(get_sessions),
) -> dict[str, bool]:
    store.upsert_apns_device(
        token=payload.token,
        bundle_id=payload.bundle_id,
        environment=payload.environment,
    )
    # Surface whether the server can actually deliver — lets the app show
    # "notifications need server setup" instead of failing silently.
    from ...push import apns as push_apns

    return {"ok": True, "deliverable": push_apns.is_configured()}


@router.delete("/apns/register")
def apns_unregister(
    payload: ApnsUnregisterPayload,
    store: SessionStore = Depends(get_sessions),
) -> dict[str, bool]:
    removed = store.delete_apns_device(payload.token)
    if not removed:
        raise HTTPException(status_code=404, detail="device not registered")
    return {"ok": True}
