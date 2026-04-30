"""Generic credentials store: list / set / delete arbitrary secrets.

This is the management surface for skill-required keys and any other
ad-hoc secrets the agent or user needs. Provider keys keep using the
existing ``/providers/{name}/key`` endpoints — those are tied to the
provider config schema (``use_inline_key`` toggle) and listing in the
``Models`` settings tab.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, status

router = APIRouter()

_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


@router.get("/credentials")
async def list_credentials() -> list[dict[str, Any]]:
    from ... import secrets as _secrets

    return _secrets.list_all()


@router.put("/credentials/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def set_credential(name: str, body: dict[str, Any]) -> None:
    from ... import secrets as _secrets

    if not _NAME_RE.match(name):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="name must match ^[A-Z][A-Z0-9_]*$",
        )
    value = body.get("value")
    if not isinstance(value, str) or not value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="value (non-empty string) is required",
        )
    kind = body.get("kind") or "generic"
    skill = body.get("skill")
    if kind not in ("generic", "skill", "provider"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="kind must be one of: generic, skill, provider",
        )
    _secrets.set(name, value, kind=kind, skill=skill)


@router.delete("/credentials/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(name: str) -> None:
    from ... import secrets as _secrets

    _secrets.delete(name)


@router.get("/credentials/{name}/exists")
async def credential_exists(name: str) -> dict[str, bool]:
    from ... import secrets as _secrets

    return {"exists": _secrets.exists(name)}
