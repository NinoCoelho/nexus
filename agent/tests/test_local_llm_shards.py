"""Tests for sharded (multi-part) GGUF support across local_llm.

Verifies:
* ``local_llm.shards`` parsing/grouping: complete sets collapse into one
  group, sets with holes are flagged incomplete, single files pass through.
* ``hf_search.list_repo_ggufs`` collapses sharded uploads (including repos
  with quants in subfolders) into one summed entry.
* ``downloader.start_download`` fetches every part under one task and
  flattens each file from its repo subfolder into the models dir root.
* ``manager`` derives clean base slugs and refuses to start incomplete or
  non-first shards.
* The ``/local/installed`` route groups shards into a single entry.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nexus.local_llm import downloader, manager
from nexus.local_llm.downloader import get_task, start_download
from nexus.local_llm.hf_search import list_repo_ggufs
from nexus.local_llm.shards import (
    expected_parts,
    group_files,
    parse_shard,
    shard_base,
)


# ---------------------------------------------------------------------------
# shards: parsing
# ---------------------------------------------------------------------------

class TestParseShard:
    def test_parses_sharded_filename(self) -> None:
        assert parse_shard("Qwen3-Coder-Next-Q5_K_M-00001-of-00004.gguf") == (
            "Qwen3-Coder-Next-Q5_K_M", 1, 4,
        )

    def test_parses_nested_repo_path(self) -> None:
        assert parse_shard("sub/dir/model-00003-of-00005.gguf") == ("model", 3, 5)

    def test_parses_bare_stem(self) -> None:
        assert parse_shard("model-00002-of-00002") == ("model", 2, 2)

    def test_none_for_single_file(self) -> None:
        assert parse_shard("model-Q4_K_M.gguf") is None
        assert parse_shard("model.gguf") is None

    def test_none_for_lookalike(self) -> None:
        # Wrong digit counts are not shard patterns.
        assert parse_shard("model-1-of-4.gguf") is None
        assert parse_shard("model-00001-of-00004.txt") is None


class TestShardBaseAndParts:
    def test_shard_base_from_filename(self) -> None:
        assert shard_base("Qwen3-Coder-Next-Q5_K_M-00001-of-00004.gguf") == \
            "Qwen3-Coder-Next-Q5_K_M"

    def test_shard_base_from_stem(self) -> None:
        assert shard_base("model-00004-of-00004.gguf") == "model"

    def test_shard_base_single_file(self) -> None:
        assert shard_base("model-Q4_K_M.gguf") == "model-Q4_K_M"

    def test_expected_parts_lists_all(self) -> None:
        assert expected_parts("model-00001-of-00003.gguf") == [
            "model-00001-of-00003.gguf",
            "model-00002-of-00003.gguf",
            "model-00003-of-00003.gguf",
        ]

    def test_expected_parts_none_for_single(self) -> None:
        assert expected_parts("model.gguf") is None


# ---------------------------------------------------------------------------
# shards: grouping
# ---------------------------------------------------------------------------

class TestGroupFiles:
    def test_single_files_pass_through(self) -> None:
        groups = group_files(["a.gguf", "b.gguf"])
        assert len(groups) == 2
        assert all(g.complete and len(g.parts) == 1 for g in groups)

    def test_complete_set_collapses(self) -> None:
        groups = group_files([
            "m-00001-of-00003.gguf",
            "m-00002-of-00003.gguf",
            "m-00003-of-00003.gguf",
        ])
        assert len(groups) == 1
        g = groups[0]
        assert g.first == "m-00001-of-00003.gguf"
        assert g.parts == [
            "m-00001-of-00003.gguf",
            "m-00002-of-00003.gguf",
            "m-00003-of-00003.gguf",
        ]
        assert g.complete is True

    def test_set_with_hole_is_incomplete(self) -> None:
        groups = group_files([
            "m-00001-of-00003.gguf",
            "m-00003-of-00003.gguf",
        ])
        assert len(groups) == 1
        assert groups[0].complete is False
        assert groups[0].first == "m-00001-of-00003.gguf"

    def test_missing_first_part_is_incomplete(self) -> None:
        groups = group_files([
            "m-00002-of-00002.gguf",
            "m-00003-of-00002.gguf",
        ])
        assert len(groups) == 1
        assert groups[0].complete is False
        assert groups[0].first == "m-00002-of-00002.gguf"

    def test_mixed_repo(self) -> None:
        groups = group_files([
            "solo.gguf",
            "m-00002-of-00002.gguf",
            "m-00001-of-00002.gguf",
        ])
        assert len(groups) == 2
        by_first = {g.first: g for g in groups}
        assert by_first["m-00001-of-00002.gguf"].parts == [
            "m-00001-of-00002.gguf", "m-00002-of-00002.gguf",
        ]
        assert by_first["solo.gguf"].complete is True


# ---------------------------------------------------------------------------
# hf_search: grouped repo listing
# ---------------------------------------------------------------------------

def _make_path_info(path: str, size: int):
    info = MagicMock()
    info.path = path
    info.size = size
    return info


class TestListRepoGgufsShards:
    def test_sharded_repo_collapses_to_one_entry(self) -> None:
        files = [
            "Qwen3-Coder-Next-Q5_K_M/Qwen3-Coder-Next-Q5_K_M-00001-of-00004.gguf",
            "Qwen3-Coder-Next-Q5_K_M/Qwen3-Coder-Next-Q5_K_M-00002-of-00004.gguf",
            "Qwen3-Coder-Next-Q5_K_M/Qwen3-Coder-Next-Q5_K_M-00003-of-00004.gguf",
            "Qwen3-Coder-Next-Q5_K_M/Qwen3-Coder-Next-Q5_K_M-00004-of-00004.gguf",
        ]
        sizes = [16, 16, 16, 4]  # GB
        mock_api = MagicMock()
        mock_api.list_repo_files.return_value = iter(files)
        mock_api.get_paths_info.return_value = iter(
            _make_path_info(f, s * 1_000_000_000) for f, s in zip(files, sizes)
        )

        with patch("huggingface_hub.HfApi", return_value=mock_api):
            results = list_repo_ggufs("owner/qwen3-coder-next-gguf")

        assert len(results) == 1
        entry = results[0]
        assert entry["filename"] == files[0]
        assert entry["size_bytes"] == 52 * 1_000_000_000
        assert entry["is_shard_group"] is True
        assert entry["parts"] == files
        assert entry["quant_label"] == "Q5_K_M"

    def test_single_files_unchanged_shape(self) -> None:
        files = ["model-Q4_K_M.gguf"]
        mock_api = MagicMock()
        mock_api.list_repo_files.return_value = iter(files)
        mock_api.get_paths_info.return_value = iter([_make_path_info(files[0], 100)])

        with patch("huggingface_hub.HfApi", return_value=mock_api):
            results = list_repo_ggufs("owner/model")

        assert len(results) == 1
        entry = results[0]
        assert entry["filename"] == "model-Q4_K_M.gguf"
        assert entry["size_bytes"] == 100
        assert entry["is_shard_group"] is False
        assert entry["parts"] == ["model-Q4_K_M.gguf"]


# ---------------------------------------------------------------------------
# downloader: multi-part tasks + flattening
# ---------------------------------------------------------------------------

def _fake_hub_download(repo_id: str, filename: str, local_dir: str, **kwargs) -> str:
    """Write a fake 100-byte file at the repo-relative path and return it."""
    dest = Path(local_dir) / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"x" * 100)
    return str(dest)


def _wait_done(task_id: str, timeout: float = 5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = get_task(task_id)
        if task.status in ("done", "error", "cancelled"):
            return task
        time.sleep(0.01)
    raise AssertionError(f"download task {task_id} did not finish in {timeout}s")


class TestDownloaderShards:
    def test_downloads_all_parts_and_flattens(self, tmp_path: Path) -> None:
        parts = [
            "sub/m-00001-of-00003.gguf",
            "sub/m-00002-of-00003.gguf",
            "sub/m-00003-of-00003.gguf",
        ]
        with (
            patch("huggingface_hub.hf_hub_download", side_effect=_fake_hub_download),
            patch("huggingface_hub.HfApi"),
        ):
            task = start_download("owner/repo", parts, tmp_path)
            _wait_done(task.task_id)

        assert task.status == "done"
        assert task.filename == parts[0]
        assert task.parts == parts
        assert task.downloaded_bytes == 300
        # Every part flattened to the root…
        for part in parts:
            flat = tmp_path / Path(part).name
            assert flat.is_file(), f"{flat} missing"
            assert flat.stat().st_size == 100
        # …and the repo subfolder pruned.
        assert not (tmp_path / "sub").exists()

    def test_single_file_download(self, tmp_path: Path) -> None:
        with (
            patch("huggingface_hub.hf_hub_download", side_effect=_fake_hub_download),
            patch("huggingface_hub.HfApi"),
        ):
            task = start_download("owner/repo", ["model.gguf"], tmp_path)
            _wait_done(task.task_id)

        assert task.status == "done"
        assert task.downloaded_bytes == 100
        assert (tmp_path / "model.gguf").is_file()

    def test_empty_filenames_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            start_download("owner/repo", [], tmp_path)

    def test_error_recorded(self, tmp_path: Path) -> None:
        def boom(*args, **kwargs):
            raise RuntimeError("network down")

        with (
            patch("huggingface_hub.hf_hub_download", side_effect=boom),
            patch("huggingface_hub.HfApi"),
        ):
            task = start_download("owner/repo", ["model.gguf"], tmp_path)
            _wait_done(task.task_id)

        assert task.status == "error"
        assert "network down" in (task.error or "")


class TestDownloaderCancellation:
    """Cancellation for Xet-backed transfers, where callback exceptions are
    swallowed by the Rust engine and only ``abort_xet_session`` works."""

    @staticmethod
    def _xet_like_download(abort_event: threading.Event):
        """Mimic a Xet transfer: tqdm exceptions are swallowed, the download
        only stops when the abort lands (watchdog → _abort_xet)."""

        def fake(repo_id: str, filename: str, local_dir: str, tqdm_class=None, **kwargs) -> str:
            dest = Path(local_dir) / filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            bar = tqdm_class(total=20) if tqdm_class is not None else None
            with dest.open("wb") as f:
                for _ in range(20):
                    if abort_event.is_set():
                        raise RuntimeError(
                            "Operation cancelled: Task cancelled: task 7 was cancelled"
                        )
                    if bar is not None:
                        try:
                            bar.update(1)
                        except Exception:
                            pass  # what the Rust engine does with our raise
                    f.write(b"x")
                    time.sleep(0.05)
            return str(dest)

        return fake

    def test_cancel_aborts_xet_transfer(self, tmp_path: Path) -> None:
        abort_event = threading.Event()

        with (
            patch.object(downloader, "_abort_xet", side_effect=abort_event.set),
            patch(
                "huggingface_hub.hf_hub_download",
                side_effect=self._xet_like_download(abort_event),
            ),
            patch("huggingface_hub.HfApi"),
        ):
            task = start_download("owner/repo", ["model.gguf"], tmp_path)
            time.sleep(0.3)  # a few chunks flow first
            assert downloader.cancel_download(task.task_id) is True
            _wait_done(task.task_id)

        assert task.status == "cancelled"
        assert abort_event.is_set()

    def test_collateral_abort_resumes(self, tmp_path: Path) -> None:
        """A transfer killed by *another* task's abort (no cancel flag here)
        must resume and complete, not error out."""
        calls = {"n": 0}

        def fake(repo_id: str, filename: str, local_dir: str, tqdm_class=None, **kwargs) -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("Operation cancelled: task 5 was cancelled")
            dest = Path(local_dir) / filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"z" * 10)
            return str(dest)

        with (
            patch("huggingface_hub.hf_hub_download", side_effect=fake),
            patch("huggingface_hub.HfApi"),
        ):
            task = start_download("owner/repo", ["model.gguf"], tmp_path)
            _wait_done(task.task_id)

        assert task.status == "done"
        assert calls["n"] == 2

    def test_cancel_between_parts_skips_rest(self, tmp_path: Path) -> None:
        downloaded: list[str] = []

        def fake(repo_id: str, filename: str, local_dir: str, tqdm_class=None, **kwargs) -> str:
            downloaded.append(filename)
            # Cancel while the worker sits between parts 1 and 2.
            running = [t for t in downloader.list_tasks() if t.status == "downloading"]
            if running:
                downloader.cancel_download(running[0].task_id)
            return _fake_hub_download(repo_id, filename, local_dir, **kwargs)

        with (
            patch("huggingface_hub.hf_hub_download", side_effect=fake),
            patch("huggingface_hub.HfApi"),
        ):
            task = start_download("owner/repo", ["m.gguf", "mm.gguf"], tmp_path)
            _wait_done(task.task_id)

        assert task.status == "cancelled"
        assert downloaded == ["m.gguf"]

    def test_real_failure_not_retried_as_abort(self, tmp_path: Path) -> None:
        calls = {"n": 0}

        def fake(repo_id: str, filename: str, local_dir: str, tqdm_class=None, **kwargs) -> str:
            calls["n"] += 1
            raise RuntimeError("HTTP 503 service unavailable")

        with (
            patch("huggingface_hub.hf_hub_download", side_effect=fake),
            patch("huggingface_hub.HfApi"),
        ):
            task = start_download("owner/repo", ["model.gguf"], tmp_path)
            _wait_done(task.task_id)

        assert task.status == "error"
        assert calls["n"] == 1


# ---------------------------------------------------------------------------
# manager: slugs + validation
# ---------------------------------------------------------------------------

class TestSlugFor:
    def test_clean_slug_for_sharded(self) -> None:
        assert manager.slug_for("Qwen3-Coder-Next-Q5_K_M-00001-of-00004.gguf") == \
            "qwen3-coder-next-q5-k-m"

    def test_slug_for_single_unchanged(self) -> None:
        assert manager.slug_for("Model-Q4_K_M.gguf") == "model-q4-k-m"


class TestValidateShardSet:
    def test_complete_set_passes(self, tmp_path: Path) -> None:
        for i in range(1, 4):
            (tmp_path / f"m-{i:05d}-of-00003.gguf").write_bytes(b"")
        manager._validate_shard_set(tmp_path / "m-00001-of-00003.gguf")  # no raise

    def test_missing_part_raises(self, tmp_path: Path) -> None:
        (tmp_path / "m-00001-of-00003.gguf").write_bytes(b"")
        (tmp_path / "m-00003-of-00003.gguf").write_bytes(b"")
        with pytest.raises(RuntimeError, match="m-00002-of-00003.gguf"):
            manager._validate_shard_set(tmp_path / "m-00001-of-00003.gguf")

    def test_non_first_shard_raises(self, tmp_path: Path) -> None:
        for i in range(1, 3):
            (tmp_path / f"m-{i:05d}-of-00002.gguf").write_bytes(b"")
        with pytest.raises(RuntimeError, match="part 2 of 2"):
            manager._validate_shard_set(tmp_path / "m-00002-of-00002.gguf")

    def test_single_file_passes(self, tmp_path: Path) -> None:
        (tmp_path / "model.gguf").write_bytes(b"")
        manager._validate_shard_set(tmp_path / "model.gguf")  # no raise


class TestStartShardGuard:
    def test_start_refuses_incomplete_set(self, tmp_path: Path) -> None:
        first = tmp_path / "m-00001-of-00002.gguf"
        first.write_bytes(b"")
        # m-00002 missing.

        with patch.object(manager, "discover_binary", return_value=Path("/usr/bin/llama-server")):
            with pytest.raises(RuntimeError, match="missing"):
                manager.start(first)


# ---------------------------------------------------------------------------
# /local/installed route: grouped listing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_installed_route_groups_shards(tmp_path: Path, monkeypatch) -> None:
    from nexus.server.routes import local_llm as routes

    monkeypatch.setattr(routes, "_MODELS_DIR", tmp_path)
    for i in range(1, 4):
        (tmp_path / f"big-{i:05d}-of-00003.gguf").write_bytes(b"x" * 10)
    (tmp_path / "solo.gguf").write_bytes(b"x" * 5)

    results = await routes.list_installed()

    by_name = {r["filename"]: r for r in results}
    assert set(by_name) == {"big-00001-of-00003.gguf", "solo.gguf"}

    group = by_name["big-00001-of-00003.gguf"]
    assert group["is_shard_group"] is True
    assert group["parts"] == [f"big-{i:05d}-of-00003.gguf" for i in range(1, 4)]
    assert group["size_bytes"] == 30
    assert group["complete"] is True
    assert group["slug"] == "big"

    solo = by_name["solo.gguf"]
    assert solo["is_shard_group"] is False
    assert solo["parts"] == ["solo.gguf"]
    assert solo["complete"] is True


@pytest.mark.asyncio
async def test_installed_route_flags_incomplete(tmp_path: Path, monkeypatch) -> None:
    from nexus.server.routes import local_llm as routes

    monkeypatch.setattr(routes, "_MODELS_DIR", tmp_path)
    (tmp_path / "m-00001-of-00002.gguf").write_bytes(b"x" * 10)

    results = await routes.list_installed()

    assert len(results) == 1
    assert results[0]["complete"] is False
    assert results[0]["is_shard_group"] is True
