"""Routes for settings: GET/POST /settings."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..deps import get_settings_store
from ..schemas import SettingsPayload
from ..settings import SettingsStore

router = APIRouter()


@router.get("/settings", response_model=SettingsPayload)
async def get_settings(store: SettingsStore = Depends(get_settings_store)) -> SettingsPayload:
    s = store.get()
    return SettingsPayload(yolo_mode=s.yolo_mode)


@router.post("/settings", response_model=SettingsPayload)
async def update_settings(
    body: SettingsPayload,
    store: SettingsStore = Depends(get_settings_store),
) -> SettingsPayload:
    """Partial update: fields omitted in the body keep their
    current value. Returns the full post-update snapshot so the UI
    can reconcile with whatever the server actually accepted."""
    changes = {
        key: value
        for key, value in body.model_dump(exclude_unset=True).items()
        if value is not None
    }
    try:
        updated = store.update(**changes)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return SettingsPayload(yolo_mode=updated.yolo_mode)
