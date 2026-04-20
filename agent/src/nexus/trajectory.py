"""Atropos-compatible RL trajectory logger.

Records every agent turn as (state, action, reward) to
~/.nexus/trajectories/trajectories-YYYY-MM-DD.jsonl.

Enabled by setting NEXUS_TRAJECTORIES=1 or config key
nexus.agent.record_trajectories = true.
"""
import json
import time
import uuid
import threading
from datetime import datetime, timezone
from pathlib import Path


class TrajectoryLogger:
    def __init__(self, base_dir: Path = Path("~/.nexus/trajectories").expanduser()):
        self._dir = base_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def log(
        self,
        *,
        session_id: str,
        turn_index: int,
        state: dict,
        action: dict,
        reward: dict,
    ) -> None:
        record = {
            "trajectory_id": uuid.uuid4().hex,
            "session_id": session_id,
            "turn_index": turn_index,
            "timestamp": int(time.time()),
            "state": state,
            "action": action,
            "reward": reward,
        }
        today = datetime.now(timezone.utc).date().isoformat()
        path = self._dir / f"trajectories-{today}.jsonl"
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with self._lock:
            # rotate if file exceeds 100 MB
            if path.exists() and path.stat().st_size > 100 * 1024 * 1024:
                i = 1
                while (rotated := path.with_suffix(f".{i:02d}.jsonl")).exists():
                    i += 1
                path.rename(rotated)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)

    def export(self, output_path: Path, *, since_date: str | None = None) -> int:
        """Concatenate JSONL files into output_path. Returns record count."""
        files = sorted(self._dir.glob("trajectories-*.jsonl"))
        if since_date:
            files = [f for f in files if f.stem.split("-", 1)[1] >= since_date]
        count = 0
        with open(output_path, "w", encoding="utf-8") as out:
            for f in files:
                for line in f.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        out.write(line + "\n")
                        count += 1
        return count
