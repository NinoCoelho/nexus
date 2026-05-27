from __future__ import annotations

import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ..auth import (
    _get_auth_manager,
    current_user,
    require_admin,
)
from ..user_store.models import User

_VALID_ROLES = {"admin", "member", "viewer"}

router = APIRouter(prefix="/auth", tags=["auth"])


class SetupRequest(BaseModel):
    token: str
    email: str
    display_name: str


class SetupResponse(BaseModel):
    user_id: str
    session_token: str


class InviteInfoResponse(BaseModel):
    code: str
    email: str | None = None
    role: str


class RegisterRequest(BaseModel):
    code: str
    email: str
    display_name: str
    password: str | None = None


class RegisterResponse(BaseModel):
    user_id: str
    session_token: str


class LoginRequest(BaseModel):
    email: str
    password: str


class SetPasswordRequest(BaseModel):
    password: str


class SessionResponse(BaseModel):
    user_id: str
    email: str
    display_name: str
    role: str
    has_password: bool = False


class CreateInviteRequest(BaseModel):
    email: str | None = None
    role: str = "member"
    max_uses: int = 1
    expires_in_hours: float | None = None


class InviteResponse(BaseModel):
    code: str
    url: str | None = None
    email: str | None = None
    role: str
    max_uses: int
    expires_at: float | None = None


_bootstrap_token: str | None = None


def generate_bootstrap_token() -> str:
    global _bootstrap_token
    _bootstrap_token = secrets.token_urlsafe(32)
    return _bootstrap_token


def get_bootstrap_token() -> str | None:
    return _bootstrap_token


def _consume_bootstrap_token(candidate: str) -> bool:
    global _bootstrap_token
    if _bootstrap_token is None or candidate != _bootstrap_token:
        return False
    _bootstrap_token = None
    return True


@router.post("/setup", response_model=SetupResponse)
def setup(request: Request, body: SetupRequest) -> Any:
    store = request.app.state.user_store
    if store.has_any_users():
        raise HTTPException(status_code=400, detail="Setup already completed")

    client_host = request.client.host if request.client else ""
    is_loopback = client_host in ("127.0.0.1", "::1", "localhost")
    from ..middleware import _is_proxied as _proxied
    proxied = _proxied(request)

    if is_loopback and not proxied:
        pass
    elif not _consume_bootstrap_token(body.token):
        raise HTTPException(status_code=401, detail="Invalid setup token")

    user = store.create_user(
        email=body.email,
        display_name=body.display_name,
        role="admin",
    )
    mgr = _get_auth_manager(request)
    token = mgr.create_token(user.id, user.role)
    resp = SetupResponse(user_id=user.id, session_token=token)
    response = Response(
        content=resp.model_dump_json(),
        media_type="application/json",
        status_code=200,
    )
    mgr.set_session_cookie(response, token)
    store.touch_login(user.id)
    return response


@router.post("/generate-bootstrap-token")
def generate_bootstrap_token_endpoint(request: Request) -> Any:
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="Loopback only")
    store = request.app.state.user_store
    if store.has_any_users():
        raise HTTPException(status_code=400, detail="Users already exist")
    token = generate_bootstrap_token()
    return {"token": token}


@router.get("/invite/{code}", response_model=InviteInfoResponse)
def get_invite_info(code: str, request: Request) -> Any:
    store = request.app.state.user_store
    valid, err = store.validate_invite(code)
    if not valid:
        raise HTTPException(status_code=404, detail=err)
    invite = store.get_invite(code)
    return InviteInfoResponse(code=code, email=invite.email, role=invite.role)


@router.post("/register", response_model=RegisterResponse)
def register(request: Request, body: RegisterRequest) -> Any:
    store = request.app.state.user_store
    try:
        user = store.redeem_invite(body.code, body.email, body.display_name, body.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    mgr = _get_auth_manager(request)
    token = mgr.create_token(user.id, user.role)
    resp = RegisterResponse(user_id=user.id, session_token=token)
    response = Response(
        content=resp.model_dump_json(),
        media_type="application/json",
        status_code=200,
    )
    mgr.set_session_cookie(response, token)
    store.touch_login(user.id)
    return response


@router.post("/login", response_model=SessionResponse)
def login(request: Request, body: LoginRequest) -> Any:
    store = request.app.state.user_store
    user = store.authenticate(body.email, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    mgr = _get_auth_manager(request)
    token = mgr.create_token(user.id, user.role)
    resp = SessionResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        has_password=user.has_password,
    )
    response = Response(
        content=resp.model_dump_json(),
        media_type="application/json",
        status_code=200,
    )
    mgr.set_session_cookie(response, token)
    store.touch_login(user.id)
    return response


@router.post("/set-password", response_model=SessionResponse)
def set_password(
    request: Request,
    body: SetPasswordRequest,
    user: User | None = Depends(current_user),
) -> Any:
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    store = request.app.state.user_store
    updated = store.set_password(user.id, body.password)
    return SessionResponse(
        user_id=updated.id,
        email=updated.email,
        display_name=updated.display_name,
        role=updated.role,
        has_password=True,
    )


@router.get("/session", response_model=SessionResponse)
def get_session(user: User | None = Depends(current_user)) -> Any:
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return SessionResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        has_password=user.has_password,
    )


@router.post("/logout")
def logout(request: Request) -> Any:
    mgr = _get_auth_manager(request)
    response = Response(
        content='{"ok":true}',
        media_type="application/json",
        status_code=200,
    )
    mgr.clear_session_cookie(response)
    return response


class ChangeNameRequest(BaseModel):
    display_name: str


@router.post("/change-name", response_model=SessionResponse)
def change_name(
    request: Request,
    body: ChangeNameRequest,
    user: User | None = Depends(current_user),
) -> Any:
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    store = request.app.state.user_store
    updated = store.update_user(user.id, display_name=body.display_name)
    return SessionResponse(
        user_id=updated.id,
        email=updated.email,
        display_name=updated.display_name,
        role=updated.role,
    )


@router.post("/invites", response_model=InviteResponse)
def create_invite(
    request: Request,
    body: CreateInviteRequest,
    user: User | None = Depends(require_admin),
) -> Any:
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if body.role not in _VALID_ROLES:
        raise HTTPException(status_code=422, detail=f"Invalid role: {body.role!r}")
    store = request.app.state.user_store
    expires_at = None
    if body.expires_in_hours is not None:
        expires_at = time.time() + body.expires_in_hours * 3600
    invite = store.create_invite(
        created_by=user.id,
        email=body.email,
        role=body.role,
        max_uses=body.max_uses,
        expires_at=expires_at,
    )
    base = str(request.base_url).rstrip("/")
    invite_url = f"{base}/invite/{invite.code}"
    return InviteResponse(
        code=invite.code,
        url=invite_url,
        email=invite.email,
        role=invite.role,
        max_uses=invite.max_uses,
        expires_at=invite.expires_at,
    )


@router.get("/invites")
def list_invites(
    request: Request,
    user: User | None = Depends(require_admin),
) -> Any:
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    store = request.app.state.user_store
    invites = store.list_invites()
    return [
        InviteResponse(
            code=i.code,
            email=i.email,
            role=i.role,
            max_uses=i.max_uses,
            expires_at=i.expires_at,
        )
        for i in invites
    ]


@router.delete("/invites/{code}")
def revoke_invite(
    code: str,
    request: Request,
    user: User | None = Depends(require_admin),
) -> Any:
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    store = request.app.state.user_store
    revoked = store.revoke_invite(code)
    if not revoked:
        raise HTTPException(status_code=404, detail="Invite not found")
    return {"ok": True}


class UserResponse(BaseModel):
    user_id: str
    email: str
    display_name: str
    role: str
    status: str
    created_at: float
    last_login: float | None = None


class UpdateUserRequest(BaseModel):
    role: str | None = None
    status: str | None = None
    display_name: str | None = None


@router.get("/admin/users", response_model=list[UserResponse])
def admin_list_users(
    request: Request,
    user: User | None = Depends(require_admin),
) -> Any:
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    store = request.app.state.user_store
    return [
        UserResponse(
            user_id=u.id,
            email=u.email,
            display_name=u.display_name,
            role=u.role,
            status=u.status,
            created_at=u.created_at,
            last_login=u.last_login,
        )
        for u in store.list_users()
    ]


@router.patch("/admin/users/{user_id}", response_model=UserResponse)
def admin_update_user(
    user_id: str,
    body: UpdateUserRequest,
    request: Request,
    user: User | None = Depends(require_admin),
) -> Any:
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user_id == user.id:
        raise HTTPException(status_code=400, detail="Cannot modify your own account")
    updates: dict[str, Any] = {}
    if body.role is not None:
        if body.role not in _VALID_ROLES:
            raise HTTPException(status_code=422, detail=f"Invalid role: {body.role!r}")
        updates["role"] = body.role
    if body.status is not None:
        if body.status not in ("active", "suspended"):
            raise HTTPException(status_code=422, detail=f"Invalid status: {body.status!r}")
        updates["status"] = body.status
    if body.display_name is not None:
        updates["display_name"] = body.display_name
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    store = request.app.state.user_store
    updated = store.update_user(user_id, **updates)
    if updated is None:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse(
        user_id=updated.id,
        email=updated.email,
        display_name=updated.display_name,
        role=updated.role,
        status=updated.status,
        created_at=updated.created_at,
        last_login=updated.last_login,
    )


@router.delete("/admin/users/{user_id}")
def admin_delete_user(
    user_id: str,
    request: Request,
    user: User | None = Depends(require_admin),
) -> Any:
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user_id == user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    store = request.app.state.user_store
    deleted = store.delete_user(user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")
    return {"ok": True}


@router.get("/status")
def auth_status(request: Request) -> Any:
    multi_user = getattr(request.app.state, "multi_user", False)
    if not multi_user:
        return {"multi_user": False}
    store = request.app.state.user_store
    has_users = store.has_any_users()
    if not has_users:
        client_host = request.client.host if request.client else ""
        is_loopback = client_host in ("127.0.0.1", "::1", "localhost")
        from ..middleware import _is_proxied as _proxied
        token_required = not (is_loopback and not _proxied(request))
        return {
            "multi_user": True,
            "needs_setup": True,
            "setup_token_required": token_required,
        }
    mgr = _get_auth_manager(request)
    token_str = mgr.extract_token(request)
    if token_str:
        payload = mgr.verify_token(token_str)
        if payload:
            user = store.get_user(payload["sub"])
            if user and user.status == "active":
                return {
                    "multi_user": True,
                    "needs_setup": False,
                    "authenticated": True,
                    "user_id": user.id,
                    "email": user.email,
                    "display_name": user.display_name,
                    "role": user.role,
                    "has_password": user.has_password,
                }
    return {"multi_user": True, "needs_setup": False, "authenticated": False}
