"""Tests for TrajectoryLogger."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus.trajectory import TrajectoryLogger


def _make_logger(tmp_path: Path) -> TrajectoryLogger:
    return TrajectoryLogger(base_dir=tmp_path / "trajectories")


def _sample_record(
    *,
    session_id: str = "sess-1",
    turn_index: int = 0,
) -> dict:
    return dict(
        session_id=session_id,
        turn_index=turn_index,
        state={"user_message": "hello", "history_length": 0, "context": ""},
        action={"reply": "hi there", "model": "gpt-4", "iterations": 1, "tool_calls": [], "input_tokens": 10, "output_tokens": 5},
        reward={"explicit": None, "implicit": {"turn_completed": True, "tool_call_count": 0}},
    )


# ── basic log ──────────────────────────────────────────────────────────────────

def test_log_creates_file(tmp_path: Path) -> None:
    logger = _make_logger(tmp_path)
    logger.log(**_sample_record())
    files = list((tmp_path / "trajectories").glob("trajectories-*.jsonl"))
    assert len(files) == 1


def test_log_valid_json(tmp_path: Path) -> None:
    logger = _make_logger(tmp_path)
    logger.log(**_sample_record())
    f = next((tmp_path / "trajectories").glob("trajectories-*.jsonl"))
    lines = [l for l in f.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["session_id"] == "sess-1"
    assert record["turn_index"] == 0
    assert "trajectory_id" in record
    assert "timestamp" in record


def test_log_appends_multiple(tmp_path: Path) -> None:
    logger = _make_logger(tmp_path)
    logger.log(**_sample_record(session_id="a", turn_index=0))
    logger.log(**_sample_record(session_id="b", turn_index=1))
    f = next((tmp_path / "trajectories").glob("trajectories-*.jsonl"))
    lines = [l for l in f.read_text().splitlines() if l.strip()]
    assert len(lines) == 2


def test_log_unique_trajectory_ids(tmp_path: Path) -> None:
    logger = _make_logger(tmp_path)
    logger.log(**_sample_record())
    logger.log(**_sample_record())
    f = next((tmp_path / "trajectories").glob("trajectories-*.jsonl"))
    ids = [json.loads(l)["trajectory_id"] for l in f.read_text().splitlines() if l.strip()]
    assert ids[0] != ids[1]


# ── export ─────────────────────────────────────────────────────────────────────

def test_export_all(tmp_path: Path) -> None:
    logger = _make_logger(tmp_path)
    logger.log(**_sample_record(session_id="s1"))
    logger.log(**_sample_record(session_id="s2"))
    out = tmp_path / "export.jsonl"
    count = logger.export(out)
    assert count == 2
    lines = [l for l in out.read_text().splitlines() if l.strip()]
    assert len(lines) == 2


def test_export_empty(tmp_path: Path) -> None:
    logger = _make_logger(tmp_path)
    out = tmp_path / "export.jsonl"
    count = logger.export(out)
    assert count == 0
    assert out.read_text() == ""


def test_export_since_date_filter(tmp_path: Path) -> None:
    """Files with dates before the filter should be excluded."""
    traj_dir = tmp_path / "trajectories"
    traj_dir.mkdir(parents=True)
    # Write fake files with specific dates
    old_file = traj_dir / "trajectories-2024-01-01.jsonl"
    new_file = traj_dir / "trajectories-2025-06-01.jsonl"
    record = json.dumps({"trajectory_id": "a", "session_id": "s"}) + "\n"
    old_file.write_text(record)
    new_file.write_text(record)

    logger = TrajectoryLogger(base_dir=traj_dir)
    out = tmp_path / "filtered.jsonl"
    count = logger.export(out, since_date="2025-01-01")
    assert count == 1


# ── rotation ──────────────────────────────────────────────────────────────────

def test_rotation_renames_large_file(tmp_path: Path, monkeypatch) -> None:
    """Simulate a file exceeding 100 MB so rotation is triggered."""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()
    traj_dir = tmp_path / "trajectories"
    traj_dir.mkdir(parents=True)
    large_file = traj_dir / f"trajectories-{today}.jsonl"
    # Write minimal content; monkeypatch stat to report size > 100 MB
    large_file.write_text('{"x": 1}\n')

    class _FakeStat:
        st_size = 101 * 1024 * 1024

    original_stat = Path.stat

    def _patched_stat(self, *args, **kwargs):
        if self == large_file:
            return _FakeStat()
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _patched_stat)

    logger = TrajectoryLogger(base_dir=traj_dir)
    logger.log(**_sample_record())

    # The original large file should have been renamed
    rotated = traj_dir / f"trajectories-{today}.01.jsonl"
    assert rotated.exists()
    # A new file for today should have been created with the new record
    assert large_file.exists()


# ── thread safety ─────────────────────────────────────────────────────────────

def test_concurrent_writes(tmp_path: Path) -> None:
    import threading
    logger = _make_logger(tmp_path)
    errors: list[Exception] = []

    def _write() -> None:
        try:
            for i in range(10):
                logger.log(**_sample_record(session_id=f"t-{i}"))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_write) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    f = next((tmp_path / "trajectories").glob("trajectories-*.jsonl"))
    lines = [l for l in f.read_text().splitlines() if l.strip()]
    assert len(lines) == 50
