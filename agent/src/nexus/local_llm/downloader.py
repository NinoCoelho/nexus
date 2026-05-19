"""Background GGUF file downloader using huggingface_hub.

Downloads run in daemon threads. Progress is reported via a custom tqdm
class passed to ``hf_hub_download`` so the UI gets accurate byte-level
updates in real time. Downloads can be cancelled through :func:`cancel_download`.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tqdm import tqdm as _base_tqdm


@dataclass
class DownloadTask:
    """State for a single in-flight or completed download."""

    task_id: str
    repo_id: str
    filename: str
    total_bytes: int = 0
    downloaded_bytes: int = 0
    status: str = "pending"  # "pending"|"downloading"|"done"|"error"|"cancelled"
    error: str | None = None


_TASKS: dict[str, DownloadTask] = {}
_TASKS_LOCK = threading.Lock()
_CANCEL_FLAGS: dict[str, threading.Event] = {}


class _DownloadCancelled(Exception):
    pass


class _DownloadTqdm(_base_tqdm):
    """Custom tqdm that pushes progress into a DownloadTask.

    Output is suppressed (file=/dev/null) but internal counters are kept
    alive so ``self.n`` accurately reflects bytes downloaded.  Raises
    :class:`_DownloadCancelled` when the task's cancel flag is set.
    """

    _task: DownloadTask | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        import os
        kwargs["file"] = open(os.devnull, "w")  # noqa: SIM115
        kwargs["disable"] = False
        super().__init__(*args, **kwargs)
        if self._task and self.total and self.total > self._task.total_bytes:
            self._task.total_bytes = int(self.total)

    def update(self, n: int = 1, **kwargs: Any) -> bool:
        task = self._task
        if task is not None:
            flag = _CANCEL_FLAGS.get(task.task_id)
            if flag and flag.is_set():
                raise _DownloadCancelled(task.task_id)
        result = super().update(n, **kwargs)
        if task is not None:
            task.downloaded_bytes = int(self.n)
            if self.total and self.total > task.total_bytes:
                task.total_bytes = int(self.total)
            _publish({
                "kind": "local_llm.download.progress",
                "task_id": task.task_id,
                "downloaded_bytes": task.downloaded_bytes,
                "total_bytes": task.total_bytes,
            })
        return result

    def close(self) -> None:
        try:
            if self.fp:  # type: ignore[attr-defined]
                self.fp.close()
        except Exception:
            pass
        super().close()


def start_download(repo_id: str, filename: str, dest_dir: Path) -> DownloadTask:
    """Start a background download of a GGUF file from HuggingFace Hub."""
    task_id = str(uuid.uuid4())
    task = DownloadTask(task_id=task_id, repo_id=repo_id, filename=filename)

    with _TASKS_LOCK:
        _TASKS[task_id] = task
    _CANCEL_FLAGS[task_id] = threading.Event()

    try:
        from huggingface_hub import HfApi
        api = HfApi()
        paths_info = list(api.get_paths_info(repo_id, paths=[filename]))
        if paths_info:
            size = getattr(paths_info[0], "size", None)
            if size is not None:
                task.total_bytes = int(size)
    except Exception:
        pass

    t = threading.Thread(
        target=_download_worker,
        args=(task, dest_dir),
        daemon=True,
        name=f"nexus-dl-{task_id[:8]}",
    )
    t.start()
    return task


def cancel_download(task_id: str) -> bool:
    """Request cancellation of an in-flight download.

    Returns ``True`` if the task was found and signalled for cancellation,
    ``False`` otherwise.  The download thread will abort on its next chunk
    and the task status will transition to ``"cancelled"``.
    """
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
    if task is None or task.status not in ("pending", "downloading"):
        return False
    flag = _CANCEL_FLAGS.get(task_id)
    if flag is not None:
        flag.set()
    return True


def get_task(task_id: str) -> DownloadTask | None:
    """Return a task by ID, or None if not found."""
    with _TASKS_LOCK:
        return _TASKS.get(task_id)


def list_tasks() -> list[DownloadTask]:
    """Return all tracked download tasks."""
    with _TASKS_LOCK:
        return list(_TASKS.values())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _publish(event: dict[str, Any]) -> None:
    try:
        from nexus.server import event_bus
        event_bus.publish(event)
    except Exception:
        pass


def _download_worker(task: DownloadTask, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    task.status = "downloading"
    _publish({
        "kind": "local_llm.download.started",
        "task_id": task.task_id,
        "repo_id": task.repo_id,
        "filename": task.filename,
    })

    class _BoundTqdm(_DownloadTqdm):
        _task = task  # type: ignore[assignment]

    try:
        from huggingface_hub import hf_hub_download
        hf_hub_download(
            repo_id=task.repo_id,
            filename=task.filename,
            local_dir=str(dest_dir),
            resume_download=True,
            local_dir_use_symlinks=False,
            tqdm_class=_BoundTqdm,
        )
        task.downloaded_bytes = task.total_bytes
        task.status = "done"
        _publish({
            "kind": "local_llm.download.done",
            "task_id": task.task_id,
            "downloaded_bytes": task.downloaded_bytes,
            "total_bytes": task.total_bytes,
        })
    except _DownloadCancelled:
        task.status = "cancelled"
        task.error = "Cancelled"
        _publish({
            "kind": "local_llm.download.cancelled",
            "task_id": task.task_id,
        })
    except Exception as exc:
        task.status = "error"
        task.error = str(exc)
        _publish({
            "kind": "local_llm.download.error",
            "task_id": task.task_id,
            "error": task.error,
        })
    finally:
        _CANCEL_FLAGS.pop(task.task_id, None)
