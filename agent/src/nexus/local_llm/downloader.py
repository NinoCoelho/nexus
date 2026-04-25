"""Background GGUF file downloader using huggingface_hub.

Downloads run in daemon threads. Progress is polled by watching the
partial file size and published to ``nexus.server.event_bus``.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class DownloadTask:
    """State for a single in-flight or completed download."""

    task_id: str
    repo_id: str
    filename: str
    total_bytes: int = 0
    downloaded_bytes: int = 0
    status: str = "pending"  # "pending"|"downloading"|"done"|"error"
    error: str | None = None


_TASKS: dict[str, DownloadTask] = {}
_TASKS_LOCK = threading.Lock()


def start_download(repo_id: str, filename: str, dest_dir: Path) -> DownloadTask:
    """Start a background download of a GGUF file from HuggingFace Hub.

    Args:
        repo_id: HuggingFace repository ID (e.g. ``"owner/model"``).
        filename: Filename within the repository.
        dest_dir: Local directory where the file will be saved.

    Returns:
        A ``DownloadTask`` instance tracking the download.
    """
    task_id = str(uuid.uuid4())
    task = DownloadTask(task_id=task_id, repo_id=repo_id, filename=filename)

    with _TASKS_LOCK:
        _TASKS[task_id] = task

    # Pre-populate total_bytes via HfApi before spawning to give the UI
    # an immediate size estimate (best-effort; 0 if it fails).
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

    # Spin up a separate thread to poll the partial file size and emit
    # progress events while the download thread blocks on hf_hub_download.
    stop_poll = threading.Event()
    poll_thread = threading.Thread(
        target=_progress_poller,
        args=(task, dest_dir, stop_poll),
        daemon=True,
        name=f"nexus-dl-poll-{task.task_id[:8]}",
    )
    poll_thread.start()

    try:
        from huggingface_hub import hf_hub_download
        hf_hub_download(
            repo_id=task.repo_id,
            filename=task.filename,
            local_dir=str(dest_dir),
            resume_download=True,
            local_dir_use_symlinks=False,
        )
        task.downloaded_bytes = task.total_bytes
        task.status = "done"
        _publish({
            "kind": "local_llm.download.done",
            "task_id": task.task_id,
            "downloaded_bytes": task.downloaded_bytes,
            "total_bytes": task.total_bytes,
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
        stop_poll.set()
        poll_thread.join(timeout=2.0)


def _progress_poller(task: DownloadTask, dest_dir: Path, stop: threading.Event) -> None:
    """Poll the partial or final file size every 500 ms and emit progress events."""
    filename = task.filename
    # hf_hub_download may place the file in a subdirectory matching the repo structure
    # or directly in dest_dir. Check both locations.
    while not stop.is_set():
        size = _read_partial_size(dest_dir, filename)
        if size is not None and size != task.downloaded_bytes:
            task.downloaded_bytes = size
            _publish({
                "kind": "local_llm.download.progress",
                "task_id": task.task_id,
                "downloaded_bytes": task.downloaded_bytes,
                "total_bytes": task.total_bytes,
            })
        stop.wait(0.5)


def _read_partial_size(dest_dir: Path, filename: str) -> int | None:
    """Return the size of the partial or complete downloaded file, if it exists."""
    # huggingface_hub uses .incomplete suffix during download
    for candidate in [
        dest_dir / (filename + ".incomplete"),
        dest_dir / filename,
        # Some hf_hub_download versions place files inside blobs/ subdir
        *(list(dest_dir.rglob(filename + ".incomplete"))[:1]),
        *(list(dest_dir.rglob(filename))[:1]),
    ]:
        try:
            if candidate.exists():
                return candidate.stat().st_size
        except OSError:
            pass
    return None
