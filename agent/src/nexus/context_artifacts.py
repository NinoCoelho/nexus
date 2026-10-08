"""Retention for the files compaction leaves behind.

Compaction is deliberately reversible: shrunk tool results are written to
``~/.nexus/vault/.tool-cache/``, the rolling session summary to
``~/.nexus/vault/.session-memory/<sid>.md``, and every collapsed or elided
message to ``~/.nexus/vault/.session-memory/.parts/<sid>.jsonl``.

Nothing used to clean any of it up, and all three live **inside the vault**,
which Syncthing replicates (``.stignore`` excludes ``*.db*`` and runtime
files, not these). A long-lived install therefore replicated an unbounded
pile of stubbed payloads forever, and deleting a session left its summary and
its verbatim archive behind.

This module owns both halves of the fix:

* :func:`purge_session_artifacts` — drop one session's note + archive, called
  from ``SessionStore.delete``.
* :func:`sweep_context_artifacts` — age/size-bounded sweep of the tool cache,
  called from the heartbeat cleanup driver.

Everything here is best-effort. A failure to tidy up must never break a turn
or a delete, so each operation is individually guarded.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from .home import vault_session_memory, vault_tool_cache

log = logging.getLogger(__name__)

# Tool-cache entries older than this are removed. They exist so a compacted
# model can re-read a result it just lost; after a month nothing refers to
# them.
DEFAULT_MAX_AGE_DAYS = 30
# Hard ceiling on the tool cache. When exceeded, oldest-first deletion runs
# until the directory is back under budget, regardless of age.
DEFAULT_MAX_TOTAL_MB = 512
# Per-session recovery archives are the only verbatim record of summarized and
# elided messages, so they get a far longer horizon than the tool cache.
DEFAULT_PARTS_MAX_AGE_DAYS = 180


def purge_session_artifacts(session_id: str) -> int:
    """Delete the session-memory note and recovery archive for ``session_id``.

    Returns the number of files removed. Called when a session is deleted —
    otherwise the rolling summary and the verbatim archive of its collapsed
    messages stay in the (replicated) vault indefinitely.
    """
    if not session_id:
        return 0
    removed = 0
    try:
        base = vault_session_memory()
    except Exception:  # noqa: BLE001 — home resolution must never raise here
        log.debug("purge_session_artifacts: cannot resolve session-memory dir", exc_info=True)
        return 0
    for candidate in (base / f"{session_id}.md", base / ".parts" / f"{session_id}.jsonl"):
        try:
            if candidate.is_file():
                candidate.unlink()
                removed += 1
        except OSError:
            log.debug("purge_session_artifacts: failed to remove %s", candidate, exc_info=True)
    if removed:
        log.info("purged %d context artifact(s) for session %s", removed, session_id)
    return removed


def _sweep_dir(
    directory: Path,
    *,
    max_age_seconds: float,
    max_total_bytes: int | None,
) -> tuple[int, int]:
    """Age- then size-bounded cleanup of one directory. Returns (files, bytes)."""
    if not directory.is_dir():
        return 0, 0
    now = time.time()
    removed_files = 0
    removed_bytes = 0

    survivors: list[tuple[float, int, Path]] = []
    try:
        entries = list(directory.iterdir())
    except OSError:
        log.debug("sweep: cannot list %s", directory, exc_info=True)
        return 0, 0

    for path in entries:
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            continue
        age = now - stat.st_mtime
        if max_age_seconds > 0 and age > max_age_seconds:
            try:
                path.unlink()
                removed_files += 1
                removed_bytes += stat.st_size
            except OSError:
                log.debug("sweep: failed to unlink %s", path, exc_info=True)
            continue
        survivors.append((stat.st_mtime, stat.st_size, path))

    if max_total_bytes is not None and max_total_bytes > 0:
        total = sum(size for _, size, _ in survivors)
        if total > max_total_bytes:
            # Oldest first until back under budget.
            survivors.sort(key=lambda item: item[0])
            for _, size, path in survivors:
                if total <= max_total_bytes:
                    break
                try:
                    path.unlink()
                    removed_files += 1
                    removed_bytes += size
                    total -= size
                except OSError:
                    log.debug("sweep: failed to unlink %s", path, exc_info=True)

    return removed_files, removed_bytes


def sweep_context_artifacts(
    *,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    max_total_mb: int = DEFAULT_MAX_TOTAL_MB,
    parts_max_age_days: int = DEFAULT_PARTS_MAX_AGE_DAYS,
) -> dict[str, int]:
    """Bound the size and age of the compaction artifact directories.

    Returns a small stats dict (``files_removed`` / ``bytes_removed``) for
    logging. Never raises.
    """
    stats = {"files_removed": 0, "bytes_removed": 0}
    try:
        cache_files, cache_bytes = _sweep_dir(
            vault_tool_cache(),
            max_age_seconds=max_age_days * 86_400,
            max_total_bytes=max_total_mb * 1024 * 1024,
        )
        stats["files_removed"] += cache_files
        stats["bytes_removed"] += cache_bytes
    except Exception:  # noqa: BLE001 — cleanup is never fatal
        log.debug("sweep_context_artifacts: tool-cache sweep failed", exc_info=True)

    try:
        parts_files, parts_bytes = _sweep_dir(
            vault_session_memory() / ".parts",
            max_age_seconds=parts_max_age_days * 86_400,
            max_total_bytes=None,
        )
        stats["files_removed"] += parts_files
        stats["bytes_removed"] += parts_bytes
    except Exception:  # noqa: BLE001
        log.debug("sweep_context_artifacts: parts sweep failed", exc_info=True)

    if stats["files_removed"]:
        log.info(
            "context artifact sweep removed %d file(s), %d KB",
            stats["files_removed"],
            stats["bytes_removed"] // 1024,
        )
    return stats
