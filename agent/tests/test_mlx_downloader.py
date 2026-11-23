"""MLX snapshot downloader: dedup + immediate cancel.

Two failures from live use:

* a second click created a **second** task writing into the same snapshot
  directory ("apareceram 2");
* cancel only took effect at the next tqdm update — a stalled transfer
  never produced one, so the task sat at "downloading" forever after the
  user asked to cancel ("pedi pra cancelar, agora estão parados").

Now: one active task per repo (a repeat request returns the existing
task), and cancel_download flips the status immediately — the worker's
own late _finish("cancelled") is a no-op. The daemon-thread zombie from a
stalled socket dies at the HTTP layer eventually; it never blocks the UI.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from nexus.local_llm import downloader as gguf_dl
from nexus.local_llm import mlx_downloader as dl

hf = pytest.importorskip("huggingface_hub")


@pytest.fixture
def clean_tasks(tmp_path):
    """Clear the SHARED task/flag registries and hand a models root.

    mlx_downloader imports ``_TASKS``/``_CANCEL_FLAGS`` from downloader —
    rebinding one module's name would split the flag state into two dicts
    and fabricate exactly the bug these tests guard against. Clear the real
    ones (same objects both modules see) and hand ``tmp_path`` as the
    download root.
    """
    gguf_dl._TASKS.clear()
    gguf_dl._CANCEL_FLAGS.clear()
    yield tmp_path
    gguf_dl._TASKS.clear()
    gguf_dl._CANCEL_FLAGS.clear()


@pytest.fixture
def stub_download(monkeypatch):
    """Replace huggingface_hub.snapshot_download with a controllable fake."""
    holder: dict = {"calls": 0, "sleep": 0.0}

    def fake_snapshot_download(**kwargs):
        holder["calls"] += 1
        if holder["sleep"]:
            time.sleep(holder["sleep"])
        # leave a loadable-looking dir so is_mlx_snapshot passes if reached
        local = Path(kwargs.get("local_dir"))
        (local / "config.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(hf, "snapshot_download", fake_snapshot_download)
    return holder


def test_second_request_returns_existing_task(clean_tasks, stub_download, monkeypatch):
    """Double-click / double-POST must not spawn a rival writer."""
    monkeypatch.setattr(dl, "_worker", lambda task, dest_dir: None)  # keep it pending
    t1 = dl.start_snapshot_download("mlx-community/Model-4bit", dest_root=clean_tasks)
    t2 = dl.start_snapshot_download("mlx-community/Model-4bit", dest_root=clean_tasks)
    assert t1.task_id == t2.task_id
    assert len(list(dl.list_tasks())) == 1


def test_different_repos_get_different_tasks(clean_tasks, stub_download, monkeypatch):
    monkeypatch.setattr(dl, "_worker", lambda task, dest_dir: None)
    t1 = dl.start_snapshot_download("mlx-community/A", dest_root=clean_tasks)
    t2 = dl.start_snapshot_download("mlx-community/B", dest_root=clean_tasks)
    assert t1.task_id != t2.task_id


def test_cancel_flips_status_immediately_on_stalled_transfer(
    clean_tasks, stub_download, monkeypatch
):
    """A transfer stalled inside snapshot_download still cancels at once."""
    stub_download["sleep"] = 2.0  # "network" hung — no tqdm updates for 2s
    task = dl.start_snapshot_download("mlx-community/Big-Model", dest_root=clean_tasks)
    time.sleep(0.1)  # let the worker enter the fake download
    assert task.status == "downloading"

    t0 = time.monotonic()
    assert dl.cancel_download(task.task_id) is True
    assert time.monotonic() - t0 < 0.5, "cancel must not wait on the worker"
    assert task.status == "cancelled"

    # the worker's own late _finish("cancelled") must be a no-op, not a
    # downgrade or duplicate event storm; wait it out and re-check
    time.sleep(2.2)
    assert task.status == "cancelled"


def test_cancel_while_pending_never_starts(clean_tasks, stub_download):
    task = dl.start_snapshot_download("mlx-community/Pending", dest_root=clean_tasks)
    assert dl.cancel_download(task.task_id) is True
    assert task.status == "cancelled"
    time.sleep(0.2)  # worker thread (if it ran) must not flip back to downloading
    assert task.status == "cancelled"


def test_completed_download_survives_late_cancel(clean_tasks, stub_download, monkeypatch):
    """Download finished before the cancel flag: status stays done."""
    stub_download["sleep"] = 0.0
    monkeypatch.setattr(
        "nexus.local_llm.mlx_snapshot.is_mlx_snapshot", lambda d: True
    )
    task = dl.start_snapshot_download("mlx-community/Quick", dest_root=clean_tasks)
    deadline = time.monotonic() + 2
    while task.status in ("pending", "downloading") and time.monotonic() < deadline:
        time.sleep(0.05)
    assert task.status == "done"
    assert dl.cancel_download(task.task_id) is False  # not cancellable anymore
    assert task.status == "done"
