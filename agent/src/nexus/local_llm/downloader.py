"""Background GGUF file downloader using huggingface_hub.

Downloads run in daemon threads. Progress is reported via a custom tqdm
class passed to ``hf_hub_download`` so the UI gets accurate byte-level
updates in real time. Downloads can be cancelled through :func:`cancel_download`.

A task may cover several files — sharded GGUFs (``<base>-00001-of-00004.gguf`` …)
download all parts sequentially under one task, and each finished part is
flattened from its repo subfolder (``dest_dir/<subdir>/<file>.gguf``) to
``dest_dir/<file>.gguf`` so the installed-models glob (non-recursive) sees it.

Cancellation note: raising from the tqdm callback only aborts plain-HTTP
downloads. Files served from Xet storage (all large GGUFs on the Hub today)
call the progress callback from the Rust engine, which swallows callback
exceptions — so each in-flight download is guarded by a watchdog thread that
calls ``abort_xet_session()`` when the cancel flag is set. That abort is
session-global and may kill *other* concurrent Xet transfers; those tasks see
an "Operation cancelled" error without their flag set and simply resume
(Xet chunk cache makes resuming cheap).
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tqdm import tqdm as _base_tqdm

log = logging.getLogger(__name__)

# Errors matching this mean "killed by abort_xet_session", not a real failure.
_ABORT_LIKE_RE = re.compile(r"cancel|abort", re.IGNORECASE)

# How often the per-download watchdog checks for cancellation.
_WATCHDOG_INTERVAL_S = 0.5

# Retries for a part whose transfer was aborted by *another* task's cancel.
_MAX_COLLATERAL_RETRIES = 5


@dataclass
class DownloadTask:
    """State for a single in-flight or completed download.

    ``filename`` is the display name (the first part for shard groups);
    ``parts`` lists every file the task downloads.
    """

    task_id: str
    repo_id: str
    filename: str
    parts: list[str] = field(default_factory=list)
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
    alive so ``self.n`` accurately reflects bytes.  Raises
    :class:`_DownloadCancelled` when the task's cancel flag is set.
    """

    _task: DownloadTask | None = None
    # Bytes already downloaded by previous parts of a multi-part task; the
    # published progress is ``base_bytes + self.n``.
    _base_bytes: int = 0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        import os
        kwargs["file"] = open(os.devnull, "w")  # noqa: SIM115
        kwargs["disable"] = False
        super().__init__(*args, **kwargs)
        # Raise the cancel exception at most once per bar: on Xet transfers
        # the callback runs inside the Rust engine, which logs every raised
        # exception — signalling once keeps that to a single traceback.
        self._cancel_signalled = False  # type: ignore[attr-defined]
        if self._task and self.total and self.total > self._task.total_bytes:
            self._task.total_bytes = int(self.total)

    def update(self, n: int = 1, **kwargs: Any) -> bool:
        task = self._task
        if task is not None and not self._cancel_signalled:  # type: ignore[attr-defined]
            flag = _CANCEL_FLAGS.get(task.task_id)
            if flag and flag.is_set():
                self._cancel_signalled = True  # type: ignore[attr-defined]
                raise _DownloadCancelled(task.task_id)
        result = super().update(n, **kwargs)
        if task is not None:
            task.downloaded_bytes = self._base_bytes + int(self.n)
            if self.total and (self._base_bytes + self.total) > task.total_bytes:
                task.total_bytes = self._base_bytes + int(self.total)
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


def start_download(repo_id: str, filenames: list[str], dest_dir: Path) -> DownloadTask:
    """Start a background download of one or more GGUF files from HuggingFace Hub.

    ``filenames`` are repo-relative paths; the first is used as the task's
    display ``filename``. For sharded GGUFs callers pass every part.
    """
    if not filenames:
        raise ValueError("filenames must not be empty")
    task_id = str(uuid.uuid4())
    task = DownloadTask(
        task_id=task_id,
        repo_id=repo_id,
        filename=filenames[0],
        parts=list(filenames),
    )

    with _TASKS_LOCK:
        _TASKS[task_id] = task
    _CANCEL_FLAGS[task_id] = threading.Event()

    try:
        from huggingface_hub import HfApi
        api = HfApi()
        paths_info = list(api.get_paths_info(repo_id, paths=filenames))
        if paths_info:
            task.total_bytes = sum(
                int(info.size) for info in paths_info
                if getattr(info, "size", None) is not None
            )
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


def _cancelled(task_id: str) -> bool:
    flag = _CANCEL_FLAGS.get(task_id)
    return flag is not None and flag.is_set()


def _abort_xet() -> None:
    """Abort in-flight Xet transfers for this process.

    Session-global by necessity: hf_xet offers no per-task handle. Any other
    concurrent Xet download dies with an "Operation cancelled" error and is
    resumed by its own worker (Xet's chunk cache makes that cheap).
    """
    try:
        from huggingface_hub.utils._xet import abort_xet_session
        abort_xet_session()
    except Exception:
        log.debug("abort_xet_session failed", exc_info=True)


def _download_part(
    task: DownloadTask, part: str, dest_dir: Path, base_bytes: int,
) -> Path:
    """Download one part under a cancellation watchdog. Returns its local path.

    The watchdog exists because raising from the tqdm callback — the classic
    cancellation trick — only works for plain-HTTP transfers. On Xet storage
    the callback is invoked from the Rust engine, which swallows callback
    exceptions; ``abort_xet_session()`` is the only way to stop it.
    """
    from huggingface_hub import hf_hub_download

    class _BoundTqdm(_DownloadTqdm):
        _task = task  # type: ignore[assignment]
        _base_bytes = base_bytes  # type: ignore[assignment]

    stop = threading.Event()

    def _watch() -> None:
        while not stop.wait(_WATCHDOG_INTERVAL_S):
            if _cancelled(task.task_id):
                log.info(
                    "[downloader] cancel requested — aborting transfer of %s", part,
                )
                _abort_xet()
                return

    watchdog = threading.Thread(
        target=_watch, daemon=True, name=f"nexus-dl-cancel-{task.task_id[:8]}",
    )
    watchdog.start()
    try:
        return Path(hf_hub_download(
            repo_id=task.repo_id,
            filename=part,
            local_dir=str(dest_dir),
            resume_download=True,
            local_dir_use_symlinks=False,
            tqdm_class=_BoundTqdm,
        ))
    finally:
        stop.set()


def _flatten(local_path: Path, dest_dir: Path) -> None:
    """Move a downloaded file from its repo subfolder to ``dest_dir`` root.

    ``hf_hub_download`` preserves the repo's directory layout under
    ``local_dir`` (``dest_dir/Qwen3-Coder-Next-Q5_K_M/x-00001-of-00004.gguf``),
    but the installed-models listing globs ``dest_dir/*.gguf`` non-recursively.
    Moving the basename into the root keeps everything visible; empty leftover
    subfolders are pruned. A pre-existing file at the destination is replaced
    (same behavior as re-downloading a root-level file).
    """
    target = dest_dir / local_path.name
    if local_path != target:
        local_path.replace(target)
    # Prune now-empty repo subfolders, up to (but not including) dest_dir.
    parent = local_path.parent
    while parent != dest_dir and parent.is_dir():
        try:
            parent.rmdir()
        except OSError:
            break  # not empty — leave it
        parent = parent.parent


def _download_worker(task: DownloadTask, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    task.status = "downloading"
    _publish({
        "kind": "local_llm.download.started",
        "task_id": task.task_id,
        "repo_id": task.repo_id,
        "filename": task.filename,
        "parts": list(task.parts),
    })

    try:
        base_bytes = 0
        for part in task.parts:
            if _cancelled(task.task_id):
                raise _DownloadCancelled(task.task_id)

            collateral = 0
            while True:
                try:
                    local_path = _download_part(task, part, dest_dir, base_bytes)
                    break
                except _DownloadCancelled:
                    raise
                except Exception as exc:
                    if _cancelled(task.task_id):
                        # Any failure after a cancel request is the abort landing.
                        raise _DownloadCancelled(task.task_id) from exc
                    if _ABORT_LIKE_RE.search(str(exc)) and collateral < _MAX_COLLATERAL_RETRIES:
                        collateral += 1
                        log.warning(
                            "[downloader] %s interrupted (likely collateral abort from "
                            "another task's cancel); resuming (try %d/%d)",
                            part, collateral, _MAX_COLLATERAL_RETRIES,
                        )
                        time.sleep(0.5 * collateral)
                        continue
                    raise

            part_bytes = local_path.stat().st_size
            _flatten(local_path, dest_dir)
            base_bytes += part_bytes
            task.downloaded_bytes = base_bytes

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
