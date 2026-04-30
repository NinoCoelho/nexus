"""Read-only provider catalog route.

The wizard fetches the bundled provider catalog (display names, auth
methods, default models) via this endpoint instead of importing the
JSON directly so a future server-side plugin loader can extend the
catalog without a UI rebuild.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ...providers import load_catalog

router = APIRouter()


@router.get("/catalog/providers")
async def list_catalog_providers() -> list[dict[str, Any]]:
    return [entry.model_dump(exclude_none=False) for entry in load_catalog()]
