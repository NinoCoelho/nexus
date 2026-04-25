"""Routes for usage analytics: GET /insights."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..deps import get_sessions
from ..session_store import SessionStore

router = APIRouter()


@router.get("/insights")
async def get_insights(
    days: int = 30,
    model: str | None = None,
    store: SessionStore = Depends(get_sessions),
) -> dict[str, Any]:
    """Return a usage analytics report for the last ``days`` days.

    Clamps ``days`` into ``[1, 365]``. Optional ``model`` scopes to sessions
    whose persisted model slug matches exactly.
    """
    from ...insights import InsightsEngine
    days = max(1, min(int(days), 365))
    engine = InsightsEngine(store._db_path)  # InsightsEngine reads loom's schema directly
    return engine.generate(days=days, model_filter=model or None)
