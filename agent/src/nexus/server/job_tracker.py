"""In-memory registry of running background jobs (chat turns, subagents,
terminals, dreams, heartbeats) for the global running-tasks indicator.

Each job has a type, label, originating session, optional kill callback,
and a started-at timestamp. The tracker publishes ``job_started`` /
``job_done`` events to the global SSE notification channel so the UI's
spinner stays live without polling.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

log = logging.getLogger(__name__)


@dataclass
class Job:
    id: str
    type: str
    label: str
    session_id: str | None
    started_at: float = field(default_factory=time.monotonic)
    kill_fn: Callable[[], Awaitable[None]] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class JobTracker:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def start(
        self,
        *,
        type: str,
        label: str,
        session_id: str | None = None,
        kill_fn: Callable[[], Awaitable[None]] | None = None,
        extra: dict[str, Any] | None = None,
        publish_fn: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> str:
        job_id = uuid.uuid4().hex[:12]
        job = Job(
            id=job_id,
            type=type,
            label=label,
            session_id=session_id,
            kill_fn=kill_fn,
            extra=extra or {},
        )
        self._jobs[job_id] = job
        if publish_fn is not None:
            publish_fn("job_started", self._job_to_dict(job))
        return job_id

    def done(
        self,
        job_id: str,
        *,
        publish_fn: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        job = self._jobs.pop(job_id, None)
        if job is None:
            return
        if publish_fn is not None:
            publish_fn("job_done", {"job_id": job_id, "type": job.type})

    def list_jobs(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        return [
            {**self._job_to_dict(j), "elapsed_seconds": round(now - j.started_at, 1)}
            for j in self._jobs.values()
        ]

    async def kill(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None:
            return False
        if job.kill_fn is not None:
            try:
                await job.kill_fn()
            except Exception:
                log.exception("kill_fn failed for job %s", job_id)
        self._jobs.pop(job_id, None)
        return True

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    @staticmethod
    def _job_to_dict(job: Job) -> dict[str, Any]:
        return {
            "id": job.id,
            "type": job.type,
            "label": job.label,
            "session_id": job.session_id,
            "extra": job.extra,
        }
