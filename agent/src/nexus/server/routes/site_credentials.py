"""Site login credentials — list / add / delete per-site logins.

Management surface for the encrypted site credential store used by the
``site_credentials`` agent tool and browser login fills. Listings never
include passwords; the raw value only enters through the add endpoint
(Settings UI) or the masked HITL form.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

router = APIRouter()


class SiteCredentialIn(BaseModel):
    username: str
    password: str


@router.get("/site-credentials")
async def list_site_credentials() -> list[dict[str, Any]]:
    from ... import site_credentials as _store

    return _store.list_sites()


@router.put("/site-credentials/{site}", status_code=status.HTTP_204_NO_CONTENT)
async def set_site_credential(site: str, body: SiteCredentialIn) -> None:
    from ... import site_credentials as _store

    try:
        _store.save(site, body.username, body.password)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.delete("/site-credentials/{site}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_site_credential(site: str) -> None:
    from ... import site_credentials as _store

    _store.delete(site)


@router.get("/site-credentials/{site}/exists")
async def site_credential_exists(site: str) -> dict[str, bool]:
    from ... import site_credentials as _store

    return {"exists": _store.exists(site)}
