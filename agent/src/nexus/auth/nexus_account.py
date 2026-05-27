"""Nexus account integration — Firebase idToken exchange + status polling.

Talks to the Next.js website (default ``https://www.nexus-model.us``):

* ``POST /api/auth/verify`` — exchange a short-lived Firebase ID token for
  a long-lived LiteLLM API key. The key lives in ``~/.nexus/secrets.toml``
  (under ``nexus_api_key``); the rest of the user record is mirrored to
  ``~/.nexus/account.json`` (non-sensitive cache for the UI).
* ``GET /api/status?apiKey=…`` — return the current tier + spend snapshot.
  No auth header — the apiKey is the credential.

Errors raise ``NexusAccountError`` with an HTTP-friendly status. Routes
turn that into a 502 so the UI can show a clear "couldn't reach Nexus".
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .. import secrets

log = logging.getLogger(__name__)

ACCOUNT_PATH = Path.home() / ".nexus" / "account.json"
SECRET_NAME = "nexus_api_key"
BROKER_SECRET_NAME = "broker_api_key"
_REQUEST_TIMEOUT = 15.0


class NexusAccountError(Exception):
    """Raised on any failure talking to the Nexus website. Carries an
    HTTP status the route layer maps to a 502/401/etc. response.
    """

    def __init__(self, message: str, *, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


def _account_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + path


def _extract_error_detail(resp: httpx.Response) -> str:
    """Pull the website's ``{ error: "..." }`` field out of an error response.

    The Next.js routes uniformly return JSON with an ``error`` key on
    failure (including 401s for Firebase verification errors). Surfacing
    the actual message is what makes the difference between a useless
    "Firebase token rejected" and an actionable
    "FIREBASE_ADMIN_SDK_KEY not set" / "Decoding Firebase ID token failed
    because the token's kid header claim has a value that isn't part of
    Google's published key set" / etc.
    """
    try:
        payload = resp.json()
    except ValueError:
        return resp.text[:200].strip()
    if isinstance(payload, dict):
        msg = payload.get("error") or payload.get("detail") or ""
        if isinstance(msg, str) and msg:
            return msg
    return resp.text[:200].strip()


def load_account() -> dict[str, Any] | None:
    """Read the cached account record, or None if not signed in."""
    if not ACCOUNT_PATH.exists():
        return None
    try:
        with ACCOUNT_PATH.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        log.warning("[nexus_account] account.json unreadable — treating as signed-out")
        return None
    if not isinstance(data, dict):
        return None
    return data


def save_account(record: dict[str, Any]) -> None:
    """Atomically write the account record. Never contains the apiKey."""
    ACCOUNT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ACCOUNT_PATH.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(record, f, indent=2)
        os.replace(tmp, ACCOUNT_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def clear_account() -> None:
    """Drop both the apiKey and the cached account record."""
    secrets.delete(SECRET_NAME)
    if ACCOUNT_PATH.exists():
        try:
            ACCOUNT_PATH.unlink()
        except OSError:
            log.exception("[nexus_account] failed to remove account.json")


def _ensure_nexus_in_config(tier: str) -> None:
    """Add the Nexus provider and canonical model to ``config.toml``.

    Called from :func:`verify_id_token` so the Nexus model is available
    the instant the user signs in, without waiting for the background
    status-watcher's first poll cycle.

    Logic mirrors :meth:`StatusWatcher._reconcile_models` — single-model-
    per-tier policy: paid/pro tiers get the ``nexus`` model, free/trial
    get ``demo``.
    """
    from .. import config_file
    from ..config_schema import ModelEntry, ProviderConfig

    cfg = config_file.load()

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

    # Any non-free tier is considered paid → gets the "nexus" model.
    is_paid = bool(tier) and tier.strip().lower() not in ("free", "")
    canonical = "nexus" if is_paid else "demo"

    existing_nexus_ids = {
        m.id for m in cfg.models if m.provider in nexus_provider_names
    }
    if existing_nexus_ids == {canonical}:
        return

    # Drop any stale nexus models and add the canonical one.
    cfg.models = [m for m in cfg.models if m.provider not in nexus_provider_names]
    cfg.models.append(
        ModelEntry(
            id=canonical,
            provider=primary,
            model_name=canonical,
            tier="heavy" if canonical == "nexus" else "balanced",
            tags=["nexus", "hosted", "pro" if canonical == "nexus" else "free"],
        ),
    )

    if not cfg.agent.default_model:
        cfg.agent.default_model = canonical

    config_file.save(cfg)
    log.info(
        "[nexus_account] ensured nexus model in config: %s (tier=%s)",
        canonical, tier,
    )


def is_signed_in() -> bool:
    return bool(secrets.get(SECRET_NAME))


def get_api_key() -> str | None:
    return secrets.get(SECRET_NAME)


async def verify_id_token(id_token: str, *, base_url: str, store_key: bool = True) -> dict[str, Any]:
    """Exchange a Firebase ID token for a Nexus LiteLLM apiKey.

    On success: when *store_key* is True (default), stores ``apiKey`` in
    secrets, mirrors the user record to ``account.json``, and confirms the
    key with the website.  When False the token is validated and user info
    is returned but nothing is persisted — used for multi-user secondary
    accounts that only need identity verification.
    """
    if not id_token or not isinstance(id_token, str):
        raise NexusAccountError("idToken is required", status=400)

    url = _account_url(base_url, "/api/auth/verify")
    log.info("[nexus_account] verify_id_token POST %s (token len=%d)", url, len(id_token))
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.post(url, json={"idToken": id_token})
    except httpx.HTTPError as exc:
        raise NexusAccountError(f"could not reach Nexus: {exc}") from exc

    if resp.status_code >= 400:
        detail = _extract_error_detail(resp)
        log.warning(
            "[nexus_account] /api/auth/verify failed status=%d detail=%r",
            resp.status_code, detail,
        )
        message = (
            f"Nexus auth/verify rejected the token: {detail}"
            if detail else f"Nexus auth/verify returned {resp.status_code}"
        )
        status = 401 if resp.status_code == 401 else 502
        raise NexusAccountError(message, status=status)

    try:
        payload = resp.json()
    except ValueError as exc:
        raise NexusAccountError("Nexus returned non-JSON response") from exc

    api_key = payload.get("apiKey")
    user = payload.get("user") or {}
    if not api_key or not isinstance(api_key, str):
        raise NexusAccountError("Nexus response missing apiKey")

    record = {
        "uid": user.get("uid", ""),
        "email": user.get("email", ""),
        "displayName": user.get("displayName") or "",
        "tier": user.get("tier", "free"),
        "cancelsAt": user.get("cancelsAt") or None,
        "trialEnd": user.get("trialEnd") or None,
        "connected": bool(user.get("connected", False)),
        "stripeCustomerId": user.get("stripeCustomerId") or "",
        "stripeSubscriptionId": user.get("stripeSubscriptionId") or "",
        "createdAt": user.get("createdAt") or "",
        "refreshedAt": datetime.now(timezone.utc).isoformat(),
    }

    if store_key:
        secrets.set(SECRET_NAME, api_key, kind="provider")

        broker_api_key = payload.get("brokerApiKey")
        if broker_api_key and isinstance(broker_api_key, str):
            secrets.set(BROKER_SECRET_NAME, broker_api_key, kind="provider")
            log.info("[nexus_account] broker API key stored")

        save_account(record)

        # Make the Nexus model available immediately — no need to wait
        # for the background status-watcher's first poll cycle.
        user_tier = record.get("tier", "free")
        _ensure_nexus_in_config(user_tier)

        try:
            confirmed = await confirm_key(id_token, base_url=base_url)
            if confirmed:
                record["connected"] = True
                save_account(record)
        except NexusAccountError as exc:
            log.warning("[nexus_account] /api/keys/confirm failed: %s", exc)

    log.info(
        "[nexus_account] signed in (email=%s tier=%s isNew=%s store_key=%s)",
        record["email"], record["tier"], payload.get("isNew"), store_key,
    )
    return {**record, "apiKey": api_key, "isNew": bool(payload.get("isNew"))}


async def confirm_key(id_token: str, *, base_url: str) -> bool:
    """Tell the website that the desktop client has stored the apiKey.

    Marks ``connected: true`` in the website's Firestore record so its
    account page stops showing a "Connect" CTA. Returns True on the
    expected ``{"confirmed": true}`` response; raises
    :class:`NexusAccountError` for any HTTP / parsing failure so the
    caller can decide whether to surface or swallow.
    """
    if not id_token or not isinstance(id_token, str):
        raise NexusAccountError("idToken is required", status=400)

    url = _account_url(base_url, "/api/keys/confirm")
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.post(url, json={"idToken": id_token})
    except httpx.HTTPError as exc:
        raise NexusAccountError(f"could not reach Nexus: {exc}") from exc

    if resp.status_code >= 400:
        detail = _extract_error_detail(resp)
        log.warning(
            "[nexus_account] /api/keys/confirm failed status=%d detail=%r",
            resp.status_code, detail,
        )
        if resp.status_code == 401:
            raise NexusAccountError(
                f"Firebase token rejected by /api/keys/confirm: {detail}"
                if detail else "Firebase token rejected by /api/keys/confirm",
                status=401,
            )
        raise NexusAccountError(
            f"Nexus keys/confirm returned {resp.status_code}: {detail}",
            status=502,
        )

    try:
        payload = resp.json()
    except ValueError as exc:
        raise NexusAccountError("Nexus returned non-JSON response") from exc

    return bool(isinstance(payload, dict) and payload.get("confirmed"))


async def fetch_status(*, base_url: str, api_key: str) -> dict[str, Any]:
    """Call ``GET /api/status?apiKey=…`` and return the parsed payload.

    The website returns ``{tier, spend, maxBudget, remaining,
    budgetDuration, models, rpmLimit, tpmLimit, budgetResetAt}``.
    """
    if not api_key:
        raise NexusAccountError("not signed in", status=401)

    url = _account_url(base_url, "/api/status")
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.get(url, params={"apiKey": api_key})
    except httpx.HTTPError as exc:
        raise NexusAccountError(f"could not reach Nexus: {exc}") from exc

    if resp.status_code >= 400:
        detail = _extract_error_detail(resp)
        log.warning(
            "[nexus_account] /api/status failed status=%d detail=%r",
            resp.status_code, detail,
        )
        if resp.status_code in (401, 403):
            message = f"Nexus apiKey rejected: {detail}" if detail else "Nexus apiKey rejected"
            raise NexusAccountError(message, status=401)
        raise NexusAccountError(
            f"Nexus /api/status returned {resp.status_code}: {detail}", status=502,
        )

    try:
        payload = resp.json()
    except ValueError as exc:
        raise NexusAccountError("Nexus returned non-JSON response") from exc

    if not isinstance(payload, dict):
        raise NexusAccountError("Nexus /api/status returned non-object")

    models = payload.get("models")
    if isinstance(models, list):
        payload["models"] = [str(m) for m in models]
    else:
        payload["models"] = []
    return payload


async def refresh_status(*, base_url: str) -> dict[str, Any]:
    """Convenience wrapper that pulls the apiKey from secrets and
    updates ``account.json`` with the latest tier on success.
    """
    api_key = get_api_key()
    if not api_key:
        raise NexusAccountError("not signed in", status=401)
    payload = await fetch_status(base_url=base_url, api_key=api_key)

    record = load_account() or {}
    record["tier"] = payload.get("tier") or payload.get("planId") or record.get("tier") or "free"
    record["cancelsAt"] = payload.get("cancelsAt") or None
    record["trialEnd"] = payload.get("trialEnd") or None
    record["models"] = payload.get("models") or []
    record["refreshedAt"] = datetime.now(timezone.utc).isoformat()
    record["lastStatus"] = {
        k: payload.get(k)
        for k in (
            "tier", "spend", "maxBudget", "remaining", "budgetDuration",
            "models", "rpmLimit", "tpmLimit", "budgetResetAt", "features",
        )
        if k in payload
    }
    save_account(record)

    features_list = payload.get("features")
    if features_list is not None:
        from ..features import set_features
        set_features(set(features_list))

    broker_api_key = payload.get("brokerApiKey")
    if broker_api_key and isinstance(broker_api_key, str):
        existing = secrets.get(BROKER_SECRET_NAME)
        if existing != broker_api_key:
            secrets.set(BROKER_SECRET_NAME, broker_api_key, kind="provider")
            log.info("[nexus_account] broker API key updated from status poll")
            _notify_broker_key_change()

    return payload


def _notify_broker_key_change() -> None:
    try:
        from ..server.event_bus import get_loop
        loop = get_loop()
        if loop is None:
            return
    except Exception:
        return

    try:
        from ..broker.client import BrokerClient
        from ..broker.sync import sync_broker_endpoints
        import asyncio

        async def _do_sync() -> None:
            client = BrokerClient()
            if client.available:
                await sync_broker_endpoints(client)

        asyncio.ensure_future(_do_sync(), loop=loop)
    except Exception:
        log.exception("[nexus_account] broker sync trigger failed")
