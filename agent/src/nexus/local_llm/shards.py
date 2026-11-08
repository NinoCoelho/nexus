"""Helpers for multi-part (sharded) GGUF files.

Large quantizations are frequently split by the uploader into parts named
``<base>-00001-of-00004.gguf`` … ``<base>-00004-of-00004.gguf`` (llama.cpp
loads the first part and stitches the rest from the same directory). To the
user this is *one* model, so search results, downloads, and the installed
list all group shards via :func:`parse_shard` / :func:`group_files`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# e.g. "Qwen3-Coder-Next-Q5_K_M-00001-of-00004.gguf" (the ".gguf" suffix is
# optional so callers can pass bare stems, as manager.start does)
_SHARD_RE = re.compile(
    r"^(?P<base>.+)-(?P<index>\d{5})-of-(?P<count>\d{5})(?:\.gguf)?$",
    re.IGNORECASE,
)


def parse_shard(filename: str) -> tuple[str, int, int] | None:
    """Return ``(base, index, count)`` for a sharded GGUF filename.

    ``filename`` may be a bare name or a repo-relative path — only the
    basename is examined. Returns ``None`` for non-sharded files.
    """
    match = _SHARD_RE.match(Path(filename).name)
    if match is None:
        return None
    return match.group("base"), int(match.group("index")), int(match.group("count"))


def shard_base(filename: str) -> str:
    """Return the shard group base, or the file stem for non-sharded files.

    ``"Qwen3-Coder-Next-Q5_K_M-00001-of-00004.gguf"`` →
    ``"Qwen3-Coder-Next-Q5_K_M"``.
    """
    parsed = parse_shard(filename)
    if parsed is not None:
        return parsed[0]
    return Path(filename).stem


def expected_parts(filename: str) -> list[str] | None:
    """Return every part filename of a shard set, or ``None`` for single files.

    ``"model-00002-of-00003.gguf"`` → ``["model-00001-of-00003.gguf", …]``.
    Used to validate that a set is complete before spawning llama-server.
    """
    parsed = parse_shard(filename)
    if parsed is None:
        return None
    base, _index, count = parsed
    suffix = ".gguf" if filename.lower().endswith(".gguf") else ""
    return [f"{base}-{i:05d}-of-{count:05d}{suffix}" for i in range(1, count + 1)]


@dataclass
class ShardGroup:
    """One user-facing model: either a single GGUF or a complete shard set."""

    #: Repo-relative path / filename of the first present part (single files
    #: pass through unchanged). This is the handle the rest of Nexus uses to
    #: start / stop / delete the group.
    first: str
    #: All parts belonging to the group, in index order (single files: itself).
    parts: list[str] = field(default_factory=list)
    #: True when every part 1..N is present, in order, starting at 1.
    complete: bool = True


def group_files(filenames: list[str]) -> list[ShardGroup]:
    """Group a list of GGUF filenames/paths into :class:`ShardGroup` entries.

    Non-sharded files pass through as singleton groups. For a shard set the
    ``first`` entry is the lowest-index part present and ``complete`` is True
    only when parts 1..N are all there, contiguous — llama.cpp refuses to load
    a set with missing middle parts, so the UI warns and disables Start.
    """
    singles: list[ShardGroup] = []
    by_base: dict[str, list[tuple[int, str]]] = {}

    for filename in filenames:
        parsed = parse_shard(filename)
        if parsed is None:
            singles.append(ShardGroup(first=filename, parts=[filename], complete=True))
            continue
        base, index, _count = parsed
        by_base.setdefault(base, []).append((index, filename))

    groups: list[ShardGroup] = singles
    for base, indexed in by_base.items():
        indexed.sort(key=lambda pair: pair[0])
        parts = [name for _index, name in indexed]
        indices = [index for index, _name in indexed]
        # Expected for a set ending at N: 1..N contiguous. If the first shard
        # itself is missing llama.cpp cannot load the model at all; either way
        # the group is flagged incomplete so the UI can explain.
        count_declared = int(_SHARD_RE.match(Path(parts[0]).name).group("count"))  # type: ignore[union-attr]
        complete = indices == list(range(1, count_declared + 1))
        groups.append(ShardGroup(first=parts[0], parts=parts, complete=complete))

    # Stable, user-friendly ordering: by the group's display name.
    groups.sort(key=lambda g: shard_base(g.first).lower())
    return groups
