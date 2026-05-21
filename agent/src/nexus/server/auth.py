from __future__ import annotations

import logging
import secrets
import time
from pathlib import Path
from typing import Any

import jwt
from fastapi import HTTPException, Request, status
from starlette.responses import Response

from .user_store.models import User

log = logging.getLogger(__name__)

_SECRET_PATH = Path.home() / ".nexus" / "server_secret"
_COOKIE_NAME = "nexus_session"
_ALGORITHM = "HS256"
_DEFAULT_EXPIRY_SECONDS = 7 * 24 * 3600


def _get_or_create_secret() -> str:
    if _SECRET_PATH.exists():
        return _SECRET_PATH.read_text().strip()
    secret = secrets.token_urlsafe(48)
    _SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SECRET_PATH.write_text(secret)
    _SECRET_PATH.chmod(0o600)
    return secret


class AuthManager:
    def __init__(self) -> None:
        self._secret = _get_or_create_secret()

    def create_token(
        self,
        user_id: str,
        role: str,
        expires_in: int = _DEFAULT_EXPIRY_SECONDS,
    ) -> str:
        now = time.time()
        payload = {
            "sub": user_id,
            "role": role,
            "iat": int(now),
            "exp": int(now + expires_in),
        }
        return jwt.encode(payload, self._secret, algorithm=_ALGORITHM)

    def verify_token(self, token: str) -> dict[str, Any] | None:
        try:
            return jwt.decode(token, self._secret, algorithms=[_ALGORITHM])
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None

    def set_session_cookie(self, response: Response, token: str) -> None:
        response.set_cookie(
            key=_COOKIE_NAME,
            value=token,
            httponly=True,
            secure=True,
            samesite="strict",
            max_age=_DEFAULT_EXPIRY_SECONDS,
            path="/",
        )

    def clear_session_cookie(self, response: Response) -> None:
        response.delete_cookie(key=_COOKIE_NAME, path="/")

    def extract_token(self, request: Request) -> str | None:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return request.cookies.get(_COOKIE_NAME)


def _get_auth_manager(request: Request) -> AuthManager:
    mgr = getattr(request.app.state, "auth_manager", None)
    if mgr is None:
        mgr = AuthManager()
        request.app.state.auth_manager = mgr
    return mgr


def _get_user_store(request: Request):
    return request.app.state.user_store


class CurrentUser:
    """FastAPI dependency that extracts the current user from the request.

    Returns None (no error) when multi-user mode is inactive.
    Returns the User when a valid JWT is present.
    Raises 401 when multi-user mode is active but no valid JWT is found.
    """

    def __init__(self, *, optional: bool = False):
        self._optional = optional

    def __call__(self, request: Request) -> User | None:
        multi_user = getattr(request.app.state, "multi_user", False)
        if not multi_user:
            return None

        mgr = _get_auth_manager(request)
        token = mgr.extract_token(request)
        if not token:
            if self._optional:
                return None
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

        payload = mgr.verify_token(token)
        if payload is None:
            if self._optional:
                return None
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")

        store = _get_user_store(request)
        user = store.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

        if user.status != "active":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account suspended")

        return user


current_user = CurrentUser()
current_user_optional = CurrentUser(optional=True)


class RequireRole:
    """FastAPI dependency that gates access by user role."""

    def __init__(self, *roles: str):
        self._roles = set(roles)

    def __call__(self, request: Request) -> User | None:
        multi_user = getattr(request.app.state, "multi_user", False)
        if not multi_user:
            return None

        mgr = _get_auth_manager(request)
        token = mgr.extract_token(request)
        if not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

        payload = mgr.verify_token(token)
        if payload is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")

        store = _get_user_store(request)
        user = store.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

        if user.status != "active":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account suspended")

        if user.role not in self._roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )

        return user


require_admin = RequireRole("admin")
require_member = RequireRole("admin", "member")
require_any = RequireRole("admin", "member", "viewer")
