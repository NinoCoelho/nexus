"""Shared vault resource management and ACL control.

Admins share vault resources and grant access to users or roles.
Members can share their own resources (admin-curated model).
"""

from __future__ import annotations

import logging
import shutil
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from ..auth import CurrentUser
from ...home import shared_vault_root
from ...server.user_store.models import User
from ... import vault as _vault

log = logging.getLogger(__name__)

router = APIRouter(prefix="/vault/shared")


def _current_user(request: Request) -> User | None:
    if not getattr(request.app.state, "multi_user", False):
        return None
    return CurrentUser(optional=True)(request)


@router.get("")
async def list_shared(request: Request) -> dict[str, Any]:
    user = _current_user(request)
    user_store = request.app.state.user_store
    if not user:
        resources = user_store.list_shared_resources()
    else:
        resources = user_store.shared_resources_for_user(user.id, user.role)
    enriched = []
    shared_root = shared_vault_root()
    for r in resources:
        entry = {
            "id": r["id"],
            "path": r["path"],
            "owner_id": r["owner_id"],
            "created_at": r["created_at"],
        }
        full = shared_root / r["path"]
        if full.is_file():
            entry["type"] = "file"
            entry["size"] = full.stat().st_size
        elif full.is_dir():
            entry["type"] = "dir"
        else:
            entry["type"] = "missing"
        if user:
            owner = user_store.get_user(r["owner_id"])
            entry["owner_name"] = owner.display_name if owner else "unknown"
        enriched.append(entry)
    return {"resources": enriched}


@router.post("", status_code=status.HTTP_201_CREATED)
async def share_resource(request: Request) -> dict[str, Any]:
    user = _current_user(request)
    if not user or user.role not in ("admin", "member"):
        raise HTTPException(status_code=403, detail="only admin or member can share")
    body = await request.json()
    source_path = body.get("path")
    if not source_path:
        raise HTTPException(status_code=400, detail="path is required")
    access_level = body.get("access_level", "read")
    grantee_type = body.get("grantee_type", "role")
    grantee_id = body.get("grantee_id", "member")

    src = _vault._vault_root() / source_path
    if not src.exists():
        raise HTTPException(status_code=404, detail=f"source path not found: {source_path}")

    shared_root = shared_vault_root()
    dest = shared_root / source_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise HTTPException(status_code=409, detail="resource already shared at this path")

    if src.is_dir():
        shutil.copytree(str(src), str(dest))
    else:
        shutil.copy2(str(src), str(dest))

    user_store = request.app.state.user_store
    resource = user_store.create_shared_resource(source_path, user.id)
    user_store.set_acl(source_path, grantee_type, grantee_id, access_level, user.id)

    return {"id": resource["id"], "path": source_path, "status": "shared"}


@router.delete("/{path:path}", status_code=status.HTTP_204_NO_CONTENT)
async def unshare_resource(path: str, request: Request) -> None:
    user = _current_user(request)
    if not user or user.role != "admin":
        raise HTTPException(status_code=403, detail="only admin can unshare")

    user_store = request.app.state.user_store
    if not user_store.delete_shared_resource(path):
        raise HTTPException(status_code=404, detail="shared resource not found")

    shared_root = shared_vault_root()
    target = shared_root / path
    if target.is_dir():
        shutil.rmtree(str(target), ignore_errors=True)
    elif target.exists():
        target.unlink()


@router.get("/{path:path}/acl")
async def get_acl(path: str, request: Request) -> dict[str, Any]:
    user = _current_user(request)
    if not user or user.role != "admin":
        raise HTTPException(status_code=403, detail="only admin can view ACL")
    user_store = request.app.state.user_store
    acls = user_store.list_acl(path)
    return {"acl": acls}


@router.put("/{path:path}/acl", status_code=status.HTTP_204_NO_CONTENT)
async def update_acl(path: str, request: Request) -> None:
    user = _current_user(request)
    if not user or user.role != "admin":
        raise HTTPException(status_code=403, detail="only admin can modify ACL")
    body = await request.json()
    user_store = request.app.state.user_store
    for entry in body.get("entries", []):
        if entry.get("remove"):
            user_store.remove_acl(path, entry["grantee_type"], entry["grantee_id"])
        else:
            user_store.set_acl(
                path,
                entry["grantee_type"],
                entry["grantee_id"],
                entry.get("access_level", "read"),
                user.id,
            )


@router.get("/{path:path}/raw")
async def read_shared_file(path: str, request: Request) -> dict[str, Any]:
    user = _current_user(request)
    user_store = request.app.state.user_store
    if user:
        if not user_store.check_access(path, user.id, user.role, "read"):
            raise HTTPException(status_code=403, detail="no read access")
    shared_root = shared_vault_root()
    full = shared_root / path
    if not full.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    text = full.read_text(encoding="utf-8", errors="replace")
    return {"path": path, "content": text}
