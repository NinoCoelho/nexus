"""Recursive walk over a folder, yielding indexable text files.

Honors a ``.gitignore`` at the folder root if ``pathspec`` is available;
otherwise falls back to a basic exclude list for the common noise dirs.
Always skips the hidden ``.nexus-graph/`` directory at the folder root.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from ._storage import HIDDEN_DIR, normalize_folder

INDEXABLE_EXTENSIONS = {".md", ".markdown", ".txt"}

_DEFAULT_EXCLUDES = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".tox", "dist", "build", ".idea", ".vscode",
    HIDDEN_DIR,
}


def _load_gitignore(folder: Path):
    gi = folder / ".gitignore"
    if not gi.is_file():
        return None
    try:
        import pathspec
    except ImportError:
        return None
    try:
        text = gi.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return pathspec.PathSpec.from_lines("gitwildmatch", text.splitlines())


def iter_indexable_files(folder: str | Path) -> Iterator[tuple[str, Path, float, int]]:
    """Yield ``(rel_posix_path, abs_path, mtime, size)`` for indexable files.

    ``rel_posix_path`` uses forward slashes and is folder-relative — this is
    the source_path stored in the GraphRAG DB so the index stays portable.
    """
    folder_p = normalize_folder(folder)
    spec = _load_gitignore(folder_p)
    folder_str = str(folder_p)

    for path in folder_p.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(folder_p)
        except ValueError:
            continue

        # Skip the hidden index directory itself, and common noise dirs.
        parts = rel.parts
        if any(p in _DEFAULT_EXCLUDES for p in parts):
            continue

        if path.suffix.lower() not in INDEXABLE_EXTENSIONS:
            continue

        if spec is not None:
            # pathspec expects POSIX-style relative paths
            rel_posix = rel.as_posix()
            if spec.match_file(rel_posix):
                continue

        try:
            stat = path.stat()
        except OSError:
            continue

        yield (rel.as_posix(), path, stat.st_mtime, stat.st_size)
