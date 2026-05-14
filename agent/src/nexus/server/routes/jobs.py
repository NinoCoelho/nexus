"""Running-jobs API: list active background tasks and kill them."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_job_tracker
from ..job_tracker import JobTracker

router = APIRouter()


@router.get("/jobs")
async def list_jobs(
    tracker: JobTracker = Depends(get_job_tracker),
) -> dict:
    return {"jobs": tracker.list_jobs()}


@router.post("/jobs/{job_id}/kill")
async def kill_job(
    job_id: str,
    tracker: JobTracker = Depends(get_job_tracker),
) -> dict:
    ok = await tracker.kill(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="job not found or not killable")
    return {"ok": True}
