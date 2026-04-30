"""OAuth flows for provider sign-in (PR 4).

Two flavors share one entry point:

* ``device`` — IETF RFC 8628 device authorization grant. The wizard
  shows the user a verification URI + user_code, the client polls
  ``/auth/oauth/poll`` until the upstream returns tokens. Used by
  providers that don't accept localhost redirect URIs (Anthropic
  Pro/Max, Amazon Q).

* ``redirect`` — standard authorization-code with PKCE. The wizard
  opens the upstream auth URL in a new tab, the upstream redirects
  back to ``http://localhost:18989/auth/callback?code=...&state=...``,
  this server exchanges the code for tokens, stores them in the
  secrets vault, and the wizard's poller picks up completion. Used
  by GitHub Copilot, Google, Vercel, etc.

State store: ``app.state.oauth_sessions`` — an in-memory dict keyed
by a session id we generate. Each entry has a 10-minute TTL; expired
entries are reaped lazily on every request. The callback endpoint is
loopback-only at the route level (the existing
``LoopbackOrTokenMiddleware`` already gates the rest), to prevent a
malicious tunnel client from completing someone else's flow.

Tokens land in ``secrets.toml`` via ``secrets.set_oauth(...)`` (PR 1).
The wizard's apply step then sets ``oauth_token_ref`` on the
provider config so the runtime can refresh tokens later.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets as py_secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from starlette.responses import HTMLResponse

from ... import secrets
from ...providers import find as find_catalog_entry
from ..deps import get_app_state

log = logging.getLogger(__name__)

router = APIRouter()

_SESSION_TTL_SECONDS = 600  # 10 minutes


@dataclass
class _OAuthSession:
    """In-flight OAuth flow state. Lives in ``app.state.oauth_sessions``."""

    flow: str  # "device" | "redirect"
    catalog_id: str
    method_id: str
    created_at: float
    # Common
    token_url: str
    client_id: str
    # Device flow
    device_code: str = ""
    user_code: str = ""
    verification_uri: str = ""
    interval: int = 5
    # Redirect flow
    state: str = ""
    code_verifier: str = ""
    redirect_uri: str = ""
    scopes: list[str] = field(default_factory=list)
    # Result
    bundle: secrets.OAuthBundle | None = None
    error: str | None = None


def _store(app_state: dict[str, Any]) -> dict[str, _OAuthSession]:
    s = app_state.setdefault("oauth_sessions", {})
    # Lazy GC of expired entries; cheap because the dict is small.
    now = time.time()
    for sid in list(s.keys()):
        if now - s[sid].created_at > _SESSION_TTL_SECONDS:
            del s[sid]
    return s


def _new_session_id() -> str:
    return py_secrets.token_urlsafe(16)


def _pkce_pair() -> tuple[str, str]:
    """Return (verifier, challenge) for PKCE S256."""
    verifier = py_secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _server_port() -> int:
    import os

    raw = os.environ.get("NEXUS_PORT", "").strip()
    if raw.isdigit():
        return int(raw)
    return 18989


def _redirect_uri() -> str:
    return f"http://127.0.0.1:{_server_port()}/auth/callback"


def _resolve_method(catalog_id: str, method_id: str):
    """Look up an OAuth method from the bundled catalog. Raises 404/422."""
    entry = find_catalog_entry(catalog_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown catalog id {catalog_id!r}")
    method = next((m for m in entry.auth_methods if m.id == method_id), None)
    if method is None or method.oauth is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"auth method {method_id!r} on {catalog_id!r} is not OAuth",
        )
    if not method.oauth.client_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"OAuth client for {catalog_id!r}/{method_id!r} is not yet configured "
            "in this Nexus build",
        )
    return entry, method


@router.post("/auth/oauth/start")
async def oauth_start(
    body: dict[str, Any],
    app_state: dict[str, Any] = Depends(get_app_state),
) -> dict[str, Any]:
    """Kick off an OAuth flow.

    Body: ``{ catalog_id, auth_method_id }``.
    Response (device): ``{ session_id, flow:"device", verification_uri,
    user_code, interval }``.
    Response (redirect): ``{ session_id, flow:"redirect", authorize_url }``.
    """
    catalog_id = (body.get("catalog_id") or "").strip()
    method_id = (body.get("auth_method_id") or "").strip()
    if not catalog_id or not method_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "catalog_id and auth_method_id are required",
        )

    _entry, method = _resolve_method(catalog_id, method_id)
    spec = method.oauth
    assert spec is not None

    sessions = _store(app_state)
    sid = _new_session_id()

    if spec.flavor == "device":
        # RFC 8628 device authorization request.
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                r = await client.post(
                    spec.device_url,
                    data={"client_id": spec.client_id, "scope": " ".join(spec.scopes)},
                    headers={"Accept": "application/json"},
                )
            except httpx.HTTPError as exc:
                log.warning("oauth device-start transport error: %s", exc)
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY,
                    f"could not reach device-code endpoint: {exc!s}",
                ) from exc
        if r.status_code >= 400:
            log.warning("oauth device-start HTTP %d: %s", r.status_code, r.text[:200])
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"upstream returned {r.status_code}: {r.text[:200]}",
            )
        payload = r.json()
        session = _OAuthSession(
            flow="device",
            catalog_id=catalog_id,
            method_id=method_id,
            created_at=time.time(),
            token_url=spec.token_url,
            client_id=spec.client_id,
            device_code=payload.get("device_code", ""),
            user_code=payload.get("user_code", ""),
            verification_uri=payload.get("verification_uri")
            or payload.get("verification_uri_complete", ""),
            interval=int(payload.get("interval", 5) or 5),
            scopes=list(spec.scopes),
        )
        sessions[sid] = session
        return {
            "session_id": sid,
            "flow": "device",
            "verification_uri": session.verification_uri,
            "user_code": session.user_code,
            "interval": session.interval,
        }

    # Redirect flavor — synthesize authorize URL with PKCE state.
    verifier, challenge = _pkce_pair() if spec.pkce else ("", "")
    state = py_secrets.token_urlsafe(16)
    session = _OAuthSession(
        flow="redirect",
        catalog_id=catalog_id,
        method_id=method_id,
        created_at=time.time(),
        token_url=spec.token_url,
        client_id=spec.client_id,
        state=state,
        code_verifier=verifier,
        redirect_uri=_redirect_uri(),
        scopes=list(spec.scopes),
    )
    sessions[sid] = session
    qs: dict[str, str] = {
        "client_id": spec.client_id,
        "redirect_uri": session.redirect_uri,
        "response_type": "code",
        "scope": " ".join(spec.scopes),
        "state": state,
    }
    if spec.pkce:
        qs["code_challenge"] = challenge
        qs["code_challenge_method"] = "S256"
    authorize_url = f"{spec.auth_url}?{urlencode(qs)}"
    return {
        "session_id": sid,
        "flow": "redirect",
        "authorize_url": authorize_url,
    }


async def _exchange_device_token(session: _OAuthSession) -> dict[str, Any] | None:
    """Poll the upstream token endpoint once. Returns the JSON body when
    the user has completed the flow, or None when still pending."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(
            session.token_url,
            data={
                "client_id": session.client_id,
                "device_code": session.device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"},
        )
    if r.status_code >= 500:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"upstream returned {r.status_code}",
        )
    body: dict[str, Any] = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    err = body.get("error")
    if err in ("authorization_pending", "slow_down"):
        return None
    if err:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"OAuth provider error: {err} ({body.get('error_description', '')})",
        )
    if "access_token" not in body:
        return None
    return body


def _store_bundle_from_token_response(
    session: _OAuthSession, body: dict[str, Any]
) -> tuple[secrets.OAuthBundle, str]:
    """Build + persist an OAuthBundle. Returns (bundle, credential name)."""
    refresh = str(body.get("refresh_token", "") or "")
    access = str(body.get("access_token", "") or "")
    expires_in = int(body.get("expires_in", 0) or 0)
    expires_at = int(time.time()) + expires_in if expires_in else 0
    bundle = secrets.OAuthBundle(
        refresh=refresh,
        access=access,
        expires_at=expires_at,
        account_id=None,
    )
    # Credential name: <CATALOG_ID>_OAUTH (uppercased, hyphens to underscores).
    cred_name = f"{session.catalog_id.upper().replace('-', '_')}_OAUTH"
    secrets.set_oauth(
        cred_name,
        refresh=bundle.refresh,
        access=bundle.access,
        expires_at=bundle.expires_at,
        account_id=bundle.account_id,
    )
    return bundle, cred_name


@router.post("/auth/oauth/poll")
async def oauth_poll(
    body: dict[str, Any],
    app_state: dict[str, Any] = Depends(get_app_state),
) -> dict[str, Any]:
    """Check whether the OAuth flow has completed.

    Body: ``{ session_id }``.
    Response: ``{ status: "pending" | "ok" | "error", credential_ref?, error? }``.
    """
    sid = (body.get("session_id") or "").strip()
    sessions = _store(app_state)
    session = sessions.get(sid)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown or expired oauth session")

    # Device flow — actively poll upstream.
    if session.flow == "device":
        # If we already cached a bundle from a previous poll, return it again.
        if session.bundle is not None:
            cred_name = f"{session.catalog_id.upper().replace('-', '_')}_OAUTH"
            return {"status": "ok", "credential_ref": cred_name}
        if session.error is not None:
            return {"status": "error", "error": session.error}
        try:
            tok = await _exchange_device_token(session)
        except HTTPException as exc:
            session.error = str(exc.detail)
            return {"status": "error", "error": session.error}
        if tok is None:
            return {"status": "pending"}
        bundle, cred_name = _store_bundle_from_token_response(session, tok)
        session.bundle = bundle
        return {"status": "ok", "credential_ref": cred_name}

    # Redirect flow — the callback handler stores the result on the session.
    if session.bundle is not None:
        cred_name = f"{session.catalog_id.upper().replace('-', '_')}_OAUTH"
        return {"status": "ok", "credential_ref": cred_name}
    if session.error is not None:
        return {"status": "error", "error": session.error}
    return {"status": "pending"}


@router.get("/auth/callback")
async def oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    app_state: dict[str, Any] = Depends(get_app_state),
) -> HTMLResponse:
    """OAuth redirect target. Loopback-only — same-host enforcement at the
    route level prevents a tunnel client from completing someone else's flow.
    """
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "callback is loopback-only")
    if request.headers.get("x-forwarded-for") or request.headers.get("x-forwarded-host"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "callback is loopback-only")

    if error:
        return HTMLResponse(
            f"<html><body><p>OAuth error: {error}</p><p>You can close this tab.</p></body></html>",
            status_code=400,
        )
    if not code or not state:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing code or state")

    # Find the session whose state matches.
    sessions = _store(app_state)
    session = next(
        (s for s in sessions.values() if s.flow == "redirect" and s.state == state),
        None,
    )
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no matching oauth session")

    # Exchange the authorization code for tokens.
    data = {
        "grant_type": "authorization_code",
        "client_id": session.client_id,
        "code": code,
        "redirect_uri": session.redirect_uri,
    }
    if session.code_verifier:
        data["code_verifier"] = session.code_verifier
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                session.token_url,
                data=data,
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        session.error = f"transport: {exc!s}"
        log.warning("oauth callback transport error: %s", exc)
        return HTMLResponse(
            "<html><body><p>OAuth exchange failed (transport).</p>"
            "<p>You can close this tab and retry from the Nexus wizard.</p></body></html>",
            status_code=502,
        )
    if r.status_code >= 400:
        session.error = f"HTTP {r.status_code}: {r.text[:200]}"
        log.warning("oauth callback HTTP %d: %s", r.status_code, r.text[:200])
        return HTMLResponse(
            f"<html><body><p>OAuth exchange failed: HTTP {r.status_code}.</p>"
            "<p>You can close this tab and retry from the Nexus wizard.</p></body></html>",
            status_code=502,
        )
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if "access_token" not in body:
        session.error = f"unexpected token response: {body!r}"
        return HTMLResponse(
            "<html><body><p>OAuth exchange completed but no access token was returned.</p>"
            "<p>You can close this tab and retry from the Nexus wizard.</p></body></html>",
            status_code=502,
        )
    bundle, _cred_name = _store_bundle_from_token_response(session, body)
    session.bundle = bundle
    return HTMLResponse(
        "<html><body><p>Sign-in complete. You can close this tab.</p>"
        "<script>window.close();</script></body></html>"
    )
