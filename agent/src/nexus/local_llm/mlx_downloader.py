"""Download MLX model snapshots from the Hugging Face Hub.

Deliberately thin: it reuses :mod:`nexus.local_llm.downloader`'s task registry,
progress tqdm, cancellation flags and event publishing rather than
reimplementing them. Those internals encode hard-won behaviour — notably
``_abort_xet()``, which is the only reliable way to interrupt an in-flight Xet
transfer — and the UI's progress component already consumes the resulting
events, so sharing them means no new client code.

The difference from the GGUF path is the unit of work: there we download a
chosen list of files; here we fetch a whole snapshot directory
(``snapshot_download``) and the identity is the repo id.
"""

from __future__ import annotations

import logging
import threading
import uuid
from pathlib import Path
from typing import Any

from .downloader import (
    _CANCEL_FLAGS,
    _TASKS,
    _TASKS_LOCK,
    _cancelled,
    _DownloadTqdm,
    _publish,
    DownloadTask,
)
from .mlx_manager import mlx_models_dir
from .mlx_snapshot import slug_for

log = logging.getLogger(__name__)

__all__ = ["start_snapshot_download", "cancel_download", "list_tasks", "get_task"]

# What a working MLX snapshot needs. Excludes the original (unquantized)
# PyTorch weights some repos also ship, which would double the download for
# nothing: mlx-lm reads *.safetensors + the json metadata only.
SNAPSHOT_ALLOW_PATTERNS = [
    "*.json",
    "*.safetensors",
    "*.txt",
    "*.model",
    "*.jinja",
    "*.tiktoken",
]

# Never pull these even when they match an allow pattern.
SNAPSHOT_IGNORE_PATTERNS = [
    "original/**",
    "*.pth",
    "*.bin",
    "consolidated*",
]


def start_snapshot_download(repo_id: str, dest_root: Path | None = None) -> DownloadTask:
    """Begin downloading ``repo_id`` into ``<models>/mlx/<slug>/``.

    Returns immediately with a pending :class:`DownloadTask`; progress and
    terminal state are published on the event bus under the same event names
    the GGUF downloader uses, so the UI needs no new handling.
    """
    root = Path(dest_root) if dest_root is not None else mlx_models_dir()
    slug = slug_for(repo_id)
    dest_dir = root / slug

    task_id = uuid.uuid4().hex[:12]
    task = DownloadTask(
        task_id=task_id,
        repo_id=repo_id,
        filename=slug,
        parts=[],
        status="pending",
    )
    with _TASKS_LOCK:
        _TASKS[task_id] = task
        _CANCEL_FLAGS[task_id] = threading.Event()

    thread = threading.Thread(
        target=_worker,
        args=(task, dest_dir),
        name=f"mlx-download[{slug}]",
        daemon=True,
    )
    thread.start()
    return task


def _worker(task: DownloadTask, dest_dir: Path) -> None:
    """Blocking snapshot download. Runs on its own thread; never raises out."""
    from huggingface_hub import snapshot_download

    task.status = "downloading"
    # Event names and the "kind" key must match downloader.py exactly — the
    # UI's progress component listens for these and nothing else.
    _publish(
        {
            "kind": "local_llm.download.started",
            "task_id": task.task_id,
            "repo_id": task.repo_id,
            "filename": task.filename,
        }
    )

    tqdm_cls = _make_tqdm(task)
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        snapshot_download(
            repo_id=task.repo_id,
            local_dir=str(dest_dir),
            allow_patterns=SNAPSHOT_ALLOW_PATTERNS,
            ignore_patterns=SNAPSHOT_IGNORE_PATTERNS,
            tqdm_class=tqdm_cls,
        )
        if _cancelled(task.task_id):
            _finish(task, "cancelled", None)
            return

        from .mlx_snapshot import is_mlx_snapshot

        if not is_mlx_snapshot(dest_dir):
            _finish(
                task,
                "error",
                "download finished but the directory has no config.json + weights "
                "— is this an MLX repo?",
            )
            return
        _finish(task, "done", None)
    except Exception as exc:  # noqa: BLE001 - must never escape the thread
        if _cancelled(task.task_id):
            _finish(task, "cancelled", None)
            return
        log.warning("[mlx] snapshot download failed for %s", task.repo_id, exc_info=True)
        _finish(task, "error", str(exc))


def _make_tqdm(task: DownloadTask) -> Any:
    """A ``_DownloadTqdm`` subclass bound to ``task``.

    ``_DownloadTqdm`` carries its task on a class attribute, so a per-download
    subclass is how two concurrent downloads stay separated.
    """

    class _BoundTqdm(_DownloadTqdm):  # type: ignore[misc, valid-type]
        _task = task

    return _BoundTqdm


def _finish(task: DownloadTask, status: str, error: str | None) -> None:
    task.status = status
    task.error = error
    if status == "done":
        _publish(
            {
                "kind": "local_llm.download.done",
                "task_id": task.task_id,
                "downloaded_bytes": task.downloaded_bytes,
                "total_bytes": task.total_bytes,
                "filename": task.filename,
            }
        )
    elif status == "cancelled":
        _publish({"kind": "local_llm.download.cancelled", "task_id": task.task_id})
    else:
        _publish(
            {"kind": "local_llm.download.error", "task_id": task.task_id, "error": error}
        )
    log.info("[mlx] download %s: %s%s", task.repo_id, status, f" ({error})" if error else "")


def cancel_download(task_id: str) -> bool:
    """Signal cancellation. Delegates to the shared GGUF implementation."""
    from .downloader import cancel_download as _cancel

    return _cancel(task_id)


def get_task(task_id: str) -> DownloadTask | None:
    with _TASKS_LOCK:
        return _TASKS.get(task_id)


def list_tasks() -> list[DownloadTask]:
    """Only MLX tasks — those whose ``parts`` list is empty by construction."""
    with _TASKS_LOCK:
        return [t for t in _TASKS.values() if not t.parts]
