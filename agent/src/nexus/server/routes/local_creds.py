"""Local-credential adoption — read auth from another tool already on disk.

Currently exposes:

* ``POST /auth/local/claude-code/claim`` — read the OAuth bundle that
  ``claude-code`` (Anthropic's CLI) stores in macOS Keychain (or
  libsecret on Linux) under the service name ``"Claude Code-credentials"``,
  copy it into Nexus's secrets store as an OAuth bundle.

Loopback-only at the route level: a tunnel client must never be able
to silently lift the user's local credentials.

ToS note: the access token in this bundle is provisioned for use
through Anthropic's official products. Reusing it via Nexus is a
gray zone — established precedent (opencode does it openly) but not
explicitly permitted by Anthropic's Pro/Max ToS. The user opts in by
running this endpoint.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ... import secrets
from ..deps import get_app_state

log = logging.getLogger(__name__)

router = APIRouter()

_CLAUDE_CODE_KEYCHAIN_SERVICE = "Claude Code-credentials"
_CLAUDE_CODE_CREDENTIAL_NAME = "ANTHROPIC_CLAUDE_CODE"

# Codex CLI stores its auth in a plain JSON file. Two shapes:
#   {auth_mode:"ApiKey",   OPENAI_API_KEY:"sk-..."}    <- portable
#   {auth_mode:"ChatGPT",  OPENAI_API_KEY:"<jwt>"}     <- ChatGPT session,
#       only valid against chatgpt.com/backend-api/codex; we refuse to
#       claim it because it won't work against api.openai.com.
_CODEX_AUTH_PATH = "~/.codex/auth.json"
_CODEX_CREDENTIAL_NAME = "OPENAI_CODEX_LOCAL"


def _require_loopback(request: Request) -> None:
    """403 if the request didn't come from the local machine.

    Local creds are by definition local — we never want a tunnel
    client (even one that authenticated via cookie) to read them.
    """
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "loopback-only")
    if request.headers.get("x-forwarded-for") or request.headers.get("x-forwarded-host"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "loopback-only")


async def _read_keychain_macos(service: str) -> str:
    """Run ``security find-generic-password -s <service> -w`` and return
    its stdout. Raises HTTPException with a useful message on failure."""
    proc = await asyncio.create_subprocess_exec(
        "security",
        "find-generic-password",
        "-s",
        service,
        "-w",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        if "could not be found" in err.lower():
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"Claude Code keychain entry {service!r} not found — sign in to claude-code first.",
            )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"keychain read failed: {err or 'unknown error'}",
        )
    return stdout.decode("utf-8", errors="replace").strip()


async def _read_libsecret_linux(service: str) -> str:
    """Read a stored secret on Linux via ``secret-tool``. Mirrors how
    Claude Code stores credentials on Linux desktops."""
    proc = await asyncio.create_subprocess_exec(
        "secret-tool",
        "lookup",
        "service",
        service,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"libsecret entry {service!r} not found ({err or 'no output'}). "
            "Is claude-code signed in on this machine?",
        )
    return stdout.decode("utf-8", errors="replace").strip()


async def _read_credentials(service: str) -> str:
    system = platform.system()
    if system == "Darwin":
        return await _read_keychain_macos(service)
    if system == "Linux":
        return await _read_libsecret_linux(service)
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        f"local-credential adoption not implemented for {system!r} yet",
    )


@router.post("/auth/local/claude-code/claim")
async def claim_claude_code(
    request: Request,
    app_state: dict[str, Any] = Depends(get_app_state),  # noqa: ARG001
) -> dict[str, Any]:
    """Lift the OAuth bundle that ``claude-code`` already stored locally.

    Returns ``{credential_ref, subscription, expires_at}`` so the wizard
    can show a brief success card before advancing to model selection.
    """
    _require_loopback(request)

    raw = await _read_credentials(_CLAUDE_CODE_KEYCHAIN_SERVICE)
    try:
        bundle = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"keychain entry isn't JSON: {exc!s}",
        ) from exc

    oauth = bundle.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "keychain entry missing 'claudeAiOauth' — claude-code may have changed its storage format",
        )

    access = str(oauth.get("accessToken") or "")
    refresh = str(oauth.get("refreshToken") or "")
    if not access:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "claude-code bundle has no accessToken — sign in to claude-code first",
        )
    # claudeAiOauth.expiresAt is milliseconds since epoch; our OAuthBundle
    # stores seconds.
    expires_ms = int(oauth.get("expiresAt") or 0)
    expires_at = expires_ms // 1000 if expires_ms else 0
    subscription = oauth.get("subscriptionType") or "claude-code"

    secrets.set_oauth(
        _CLAUDE_CODE_CREDENTIAL_NAME,
        refresh=refresh,
        access=access,
        expires_at=expires_at,
        account_id=str(subscription) if subscription else None,
    )
    log.info(
        "claimed Claude Code credentials: subscription=%s expires_at=%s",
        subscription, expires_at,
    )
    return {
        "credential_ref": _CLAUDE_CODE_CREDENTIAL_NAME,
        "subscription": subscription,
        "expires_at": expires_at,
    }


def _codex_auth_path() -> Path:
    return Path(os.path.expanduser(_CODEX_AUTH_PATH))


@router.post("/auth/local/codex/claim")
async def claim_codex(
    request: Request,
    app_state: dict[str, Any] = Depends(get_app_state),  # noqa: ARG001
) -> dict[str, Any]:
    """Lift the API key that ``codex`` already has on disk.

    Only ``auth_mode == "ApiKey"`` is portable to api.openai.com — the
    ``ChatGPT`` session token only works against ``chatgpt.com/backend-api``
    and we refuse it with a clear message rather than silently storing
    a token that will fail on every chat call.

    Returns ``{credential_ref, auth_mode}`` so the wizard can label the
    success card. Loopback-only.
    """
    _require_loopback(request)

    p = _codex_auth_path()
    if not p.exists():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"{p} not found — sign in to codex first.",
        )
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"could not read {p}: {exc!s}",
        ) from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"{p} isn't JSON: {exc!s}",
        ) from exc

    auth_mode = (data.get("auth_mode") or "").strip()
    api_key = (data.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"{p} has no OPENAI_API_KEY — sign in to codex first.",
        )

    if auth_mode == "ChatGPT":
        # The ChatGPT-mode token authenticates against chatgpt.com only;
        # using it as a Bearer against api.openai.com returns 401. We
        # surface that here so the wizard refuses cleanly instead of the
        # user discovering it on their first chat.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "codex is signed in via ChatGPT — that token only works against "
            "chatgpt.com/backend-api and isn't usable as an OpenAI API key. "
            "Switch codex to API-key mode (or use the OpenAI catalog tile "
            "directly) and try again.",
        )

    secrets.set(_CODEX_CREDENTIAL_NAME, api_key, kind="provider")
    log.info("claimed Codex credentials: auth_mode=%s", auth_mode or "ApiKey")
    return {
        "credential_ref": _CODEX_CREDENTIAL_NAME,
        "auth_mode": auth_mode or "ApiKey",
    }
