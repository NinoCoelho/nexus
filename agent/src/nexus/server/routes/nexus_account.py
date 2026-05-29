"""Routes for the optional Nexus account integration.

All endpoints are loopback-only — same posture as the tunnel admin
routes — so even a tunnel-authenticated client can't enumerate or rotate
the user's apiKey through them.

* ``POST /auth/nexus/verify``   — exchange a Firebase idToken for an apiKey.
  The website reconciles the LiteLLM key with the Firestore tier on every
  call, so re-signing-in after an upgrade/downgrade is sufficient to sync.
* ``GET  /auth/nexus/status``   — read the cached status (no outbound call).
* ``POST /auth/nexus/refresh``  — force a fresh /api/status fetch.
* ``POST /auth/nexus/logout``   — drop the apiKey + cached account record.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ...auth import nexus_account
from ...auth.status_watcher import StatusWatcher
from ...config_schema import ModelEntry, ProviderConfig
from ..deps import get_app_state

router = APIRouter()
log = logging.getLogger(__name__)


def _require_loopback(request: Request) -> None:
    """403 if the request didn't come from the local machine.

    Mirrors the helper in ``routes/tunnel.py`` — kept duplicate (rather
    than imported) so the tunnel module isn't a hard dependency of every
    auth route.
    """
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="Nexus auth is loopback-only")
    if request.headers.get("x-forwarded-for") or request.headers.get("x-forwarded-host"):
        raise HTTPException(status_code=403, detail="Nexus auth is loopback-only")


def _watcher(request: Request) -> StatusWatcher | None:
    return getattr(request.app.state, "nexus_status_watcher", None)


def _ensure_nexus_in_memory(cfg: Any, tier: str) -> None:
    """Add the Nexus provider + ``nexus`` model to the in-memory config.
    
    Mirrors :func:`nexus_account._ensure_nexus_in_config` but operates on
    a live ``cfg`` object rather than loading/saving from disk. Called
    eagerly after login so the model slot is populated before the first
    background status poll. The status watcher will add ``nexus-vision``
    on its first tick.
    """
    nexus_provider_names = {
        name for name, p in cfg.providers.items()
        if getattr(p, "runtime_kind", "") == "nexus"
    }

    if not nexus_provider_names:
        cfg.providers["nexus"] = ProviderConfig(
            base_url="https://llm.nexus-model.us/v1",
            credential_ref="nexus_api_key",
            type="openai_compat",
            catalog_id="nexus",
            runtime_kind="nexus",
        )
        nexus_provider_names = {"nexus"}

    primary = "nexus" if "nexus" in nexus_provider_names else sorted(nexus_provider_names)[0]

    existing_nexus_ids = {
        m.id for m in cfg.models if m.provider in nexus_provider_names
    }
    if existing_nexus_ids == {"nexus"}:
        return

    cfg.models = [m for m in cfg.models if m.provider not in nexus_provider_names]
    cfg.models.append(
        ModelEntry(
            id="nexus",
            provider=primary,
            model_name="nexus",
            tier="heavy",
            tags=["nexus", "hosted", "pro"],
        ),
    )

    if not cfg.agent.default_model:
        cfg.agent.default_model = "nexus"


def _account_view(*, watcher: StatusWatcher | None) -> dict[str, Any]:
    """Build the JSON shape returned by /status and /refresh.

    Never includes the apiKey. Reads from ``account.json`` for stable
    fields (email/tier) and from the watcher's in-memory cache for the
    live spend/budget snapshot.
    """
    record = nexus_account.load_account()
    signed_in = nexus_account.is_signed_in()
    last_status = watcher.last_status if watcher is not None else None
    # Prefer the live planId/tier from the watcher's last /api/status
    # response over the cached account.json record — the cached record
    # may be stale (e.g. recorded as "free" before an upgrade).
    cached_tier = (record or {}).get("tier", "free") if signed_in else "free"
    live_tier = None
    if last_status:
        live_tier = last_status.get("planId") or last_status.get("tier")
    effective_tier = live_tier or cached_tier

    out: dict[str, Any] = {
        "signedIn": signed_in,
        "email": (record or {}).get("email", "") if signed_in else "",
        "tier": effective_tier,
        "cancelsAt": (record or {}).get("cancelsAt") or None,
        "trialEnd": (record or {}).get("trialEnd") or None,
        # The desktop has "connected" the apiKey when /api/keys/confirm
        # succeeded after sign-in. The UI surfaces this as a Connect CTA
        # when false (e.g. user signed in before the confirm path
        # existed, or confirm returned a transient error).
        "connected": bool((record or {}).get("connected", False)) if signed_in else False,
        "models": (record or {}).get("models") or [],
        "refreshedAt": (record or {}).get("refreshedAt", ""),
    }
    if last_status:
        out["status"] = last_status
    from ...features import get_features
    out["features"] = list(get_features())
    return out


@router.post("/auth/nexus/verify")
async def auth_nexus_verify(
    body: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    _require_loopback(request)
    cfg = get_app_state(request).get("cfg")
    base_url = (
        getattr(getattr(cfg, "nexus_account", None), "base_url", None)
        or "https://www.nexus-model.us"
    )
    id_token = body.get("idToken") or body.get("id_token") or ""
    invite_code = body.get("inviteCode") or body.get("invite_code") or ""

    multi_user = getattr(request.app.state, "multi_user", False)

    if multi_user:
        try:
            record = await nexus_account.verify_id_token(id_token, base_url=base_url, store_key=False)
        except nexus_account.NexusAccountError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    else:
        try:
            record = await nexus_account.verify_id_token(id_token, base_url=base_url)
        except nexus_account.NexusAccountError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

        # Eagerly ensure the nexus provider + model exist in the in-memory
        # config so they're available right away, even if the background
        # status poll hasn't run yet or fails.
        _ensure_nexus_in_memory(cfg, record.get("tier", "free"))

        watcher = _watcher(request)
        if watcher is not None:
            try:
                await watcher.tick_once()
            except nexus_account.NexusAccountError:
                log.warning("[nexus_account] post-verify status fetch failed")
                # tick_once also reconciles models + rebuilds the registry.
                # When it fails we still need to persist + rebuild so the
                # in-memory state matches what _ensure_nexus_in_memory and
                # verify_id_token wrote to disk.
                from ...config_file import save as _save_config
                from .config import _rebuild_registry
                app_state = get_app_state(request)
                _agent = getattr(request.app.state, "agent", None)
                if _agent is not None:
                    try:
                        _save_config(cfg)
                    except Exception:
                        log.exception("[nexus_account] save config fallback failed")
                    try:
                        _rebuild_registry(cfg, app_state, _agent)
                    except Exception:
                        log.exception("[nexus_account] registry rebuild fallback failed")
        return record

    from ..auth import _get_auth_manager
    from starlette.responses import Response as _Response
    import json as _json

    store = request.app.state.user_store
    nexus_uid = record.get("uid", "")
    email = record.get("email", "")
    display_name = record.get("displayName", "")

    existing = store.get_user_by_nexus_uid(nexus_uid) if nexus_uid else None

    if existing:
        if existing.status != "active":
            raise HTTPException(status_code=403, detail="Account suspended")
        is_admin = existing.role == "admin"
        if is_admin:
            api_key = record.get("apiKey")
            if api_key:
                from ... import secrets as _sec
                _sec.set(nexus_account.SECRET_NAME, api_key, kind="provider")
                nexus_account.save_account({k: v for k, v in record.items() if k != "apiKey"})
        mgr = _get_auth_manager(request)
        jwt_token = mgr.create_token(existing.id, existing.role, existing.status)
        store.touch_login(existing.id)
        resp_body = {**record, "multi_user": True, "user_id": existing.id, "role": existing.role}
        response = _Response(content=_json.dumps(resp_body), media_type="application/json")
        mgr.set_session_cookie(response, jwt_token)
        return response

    if not store.has_any_users():
        api_key = record.get("apiKey")
        if api_key:
            from ... import secrets as _sec
            _sec.set(nexus_account.SECRET_NAME, api_key, kind="provider")
            nexus_account.save_account({k: v for k, v in record.items() if k != "apiKey"})
        user = store.create_user(email=email, display_name=display_name or email, role="admin", nexus_uid=nexus_uid)
        mgr = _get_auth_manager(request)
        jwt_token = mgr.create_token(user.id, user.role, user.status)
        store.touch_login(user.id)
        resp_body = {**record, "multi_user": True, "user_id": user.id, "role": "admin"}
        response = _Response(content=_json.dumps(resp_body), media_type="application/json")
        mgr.set_session_cookie(response, jwt_token)
        return response

    if invite_code:
        valid, err = store.validate_invite(invite_code)
        if not valid:
            raise HTTPException(status_code=400, detail=err)
        try:
            user = store.redeem_invite(invite_code, email=email, display_name=display_name or email, nexus_uid=nexus_uid)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        mgr = _get_auth_manager(request)
        jwt_token = mgr.create_token(user.id, user.role, user.status)
        store.touch_login(user.id)
        resp_body = {**record, "multi_user": True, "user_id": user.id, "role": user.role}
        response = _Response(content=_json.dumps(resp_body), media_type="application/json")
        mgr.set_session_cookie(response, jwt_token)
        return response

    settings = request.app.state.settings_store.get()
    auto_accept = settings.auto_accept_members
    if auto_accept:
        user = store.create_user(email=email, display_name=display_name or email, role="member", nexus_uid=nexus_uid)
        mgr = _get_auth_manager(request)
        jwt_token = mgr.create_token(user.id, user.role, user.status)
        store.touch_login(user.id)
        resp_body = {**record, "multi_user": True, "user_id": user.id, "role": user.role, "status": "active"}
        response = _Response(content=_json.dumps(resp_body), media_type="application/json")
        mgr.set_session_cookie(response, jwt_token)
        return response
    else:
        user = store.create_user(email=email, display_name=display_name or email, role="member", status="pending", nexus_uid=nexus_uid)
        mgr = _get_auth_manager(request)
        jwt_token = mgr.create_token(user.id, user.role, "pending")
        store.touch_login(user.id)
        from ...server.event_bus import publish as _publish
        _publish({"type": "pending_users_changed"})
        resp_body = {**record, "multi_user": True, "user_id": user.id, "role": user.role, "status": "pending"}
        response = _Response(content=_json.dumps(resp_body), media_type="application/json")
        mgr.set_session_cookie(response, jwt_token)
        return response


@router.get("/auth/nexus/status")
async def auth_nexus_status(request: Request) -> dict[str, Any]:
    _require_loopback(request)
    return _account_view(watcher=_watcher(request))


@router.post("/auth/nexus/refresh")
async def auth_nexus_refresh(request: Request) -> dict[str, Any]:
    _require_loopback(request)
    if not nexus_account.is_signed_in():
        raise HTTPException(status_code=401, detail="not signed in")
    watcher = _watcher(request)
    if watcher is None:
        raise HTTPException(status_code=503, detail="status watcher not running")
    try:
        await watcher.tick_once()
    except nexus_account.NexusAccountError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return _account_view(watcher=watcher)


@router.post("/auth/nexus/logout")
async def auth_nexus_logout(request: Request) -> dict[str, Any]:
    _require_loopback(request)
    nexus_account.clear_account()
    # Reset the watcher's cached state so the UI immediately reflects sign-out.
    watcher = _watcher(request)
    if watcher is not None:
        watcher._last_status = None  # type: ignore[attr-defined]
        watcher._last_models = ()  # type: ignore[attr-defined]
    return {"signedIn": False}
