"""Vault — user-editable markdown file store under ~/.nexus/vault/."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_VAULT_ROOT = Path("~/.nexus/vault").expanduser()
_MAX_SIZE = 1 * 1024 * 1024  # 1 MiB
_SKIP_DIRS = {"node_modules", "__pycache__"}


@dataclass
class Entry:
    path: str
    type: str  # "file" | "dir"
    size: int | None = None
    mtime: float | None = None


def _vault_root() -> Path:
    _VAULT_ROOT.mkdir(parents=True, exist_ok=True)
    return _VAULT_ROOT


def _safe_resolve(rel: str, root: Path) -> Path:
    """Resolve rel path under root; raise ValueError if it escapes."""
    resolved = Path(os.path.realpath(root / rel))
    root_real = Path(os.path.realpath(root))
    try:
        resolved.relative_to(root_real)
    except ValueError:
        raise ValueError(f"path {rel!r} escapes vault root")
    return resolved


def list_tree() -> list[Entry]:
    root = _vault_root()
    root_real = Path(os.path.realpath(root))
    entries: list[Entry] = []

    def _walk(d: Path) -> None:
        try:
            children = sorted(d.iterdir())
        except PermissionError:
            return
        for child in children:
            if child.name.startswith("."):
                continue
            if child.name in _SKIP_DIRS:
                continue
            rel = str(child.relative_to(root_real))
            if child.is_dir() and not child.is_symlink():
                entries.append(Entry(path=rel, type="dir"))
                _walk(child)
            elif child.is_file():
                stat = child.stat()
                entries.append(Entry(path=rel, type="file", size=stat.st_size, mtime=stat.st_mtime))

    _walk(root_real)
    return entries


def _parse_frontmatter(content: str) -> tuple[dict[str, Any] | None, str | None]:
    """Split YAML frontmatter from the body. Returns (frontmatter_dict, body_text).
    Returns (None, None) if the file doesn't start with a --- fence."""
    if not content.startswith("---"):
        return None, None
    end = content.find("\n---", 3)
    if end == -1:
        return None, None
    fm_text = content[3:end].strip()
    body = content[end + 4:].lstrip("\n")
    try:
        fm = yaml.safe_load(fm_text)
        return (fm if isinstance(fm, dict) else None), body
    except yaml.YAMLError:
        return None, None


def resolve_path(rel_path: str) -> Path:
    """Return the absolute Path for a vault-relative path, raising if it
    escapes the vault root. Caller is responsible for checking existence
    and file type — used by endpoints that stream raw bytes."""
    return _safe_resolve(rel_path, _vault_root())


def read_file(
    rel_path: str,
    *,
    offset: int = 0,
    limit: int | None = None,
    head: int | None = None,
    tail: int | None = None,
) -> dict[str, Any]:
    """Read a vault file, optionally a slice.

    Slice modes (mutually exclusive — first match wins):
      * ``head=N``  → first N lines
      * ``tail=N``  → last N lines
      * ``offset``/``limit`` (bytes) → arbitrary byte range; ``offset+limit`` may
        exceed file size (clamped). When ``limit`` is None the rest of the file
        from ``offset`` is returned.

    The result always carries ``size`` (original file size in bytes) so callers
    can detect partial reads. ``truncated`` is set when the returned slice does
    not cover the whole file.
    """
    root = _vault_root()
    full = _safe_resolve(rel_path, root)
    if not full.is_file():
        raise FileNotFoundError(f"no such file: {rel_path!r}")

    stat = full.stat()
    size = stat.st_size

    try:
        text = full.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {
            "path": rel_path,
            "content": "",
            "binary": True,
            "size": size,
            "mtime": stat.st_mtime,
        }

    truncated = False
    slice_meta: dict[str, Any] = {}

    if head is not None and head >= 0:
        lines = text.splitlines(keepends=True)
        total_lines = len(lines)
        content = "".join(lines[:head])
        truncated = head < total_lines
        slice_meta = {"mode": "head", "lines_returned": min(head, total_lines), "total_lines": total_lines}
    elif tail is not None and tail >= 0:
        lines = text.splitlines(keepends=True)
        total_lines = len(lines)
        content = "".join(lines[-tail:] if tail > 0 else [])
        truncated = tail < total_lines
        slice_meta = {"mode": "tail", "lines_returned": min(tail, total_lines), "total_lines": total_lines}
    elif offset > 0 or limit is not None:
        if offset < 0:
            offset = 0
        if offset >= len(text):
            content = ""
            truncated = size > 0
        elif limit is None:
            content = text[offset:]
            truncated = offset > 0
        else:
            end = offset + max(0, int(limit))
            content = text[offset:end]
            truncated = offset > 0 or end < len(text)
        next_offset = offset + len(content) if truncated and limit is not None else None
        slice_meta = {
            "mode": "byte_range",
            "offset": offset,
            "bytes_returned": len(content),
        }
        if next_offset is not None and next_offset < len(text):
            slice_meta["next_offset"] = next_offset
    else:
        content = text

    result: dict[str, Any] = {
        "path": rel_path,
        "content": content,
        "size": size,
        "mtime": stat.st_mtime,
    }
    if truncated:
        result["truncated"] = True
        result["slice"] = slice_meta
    # Frontmatter parsing is only meaningful for full reads — partial reads can
    # cut the YAML block in half. Skip it for slices.
    if not truncated:
        fm, body = _parse_frontmatter(content)
        if fm is not None:
            result["frontmatter"] = fm
            result["body"] = body
    return result


def write_file(rel_path: str, content: str) -> None:
    if len(content.encode("utf-8", errors="replace")) > _MAX_SIZE:
        raise ValueError("content exceeds 1 MiB limit")
    root = _vault_root()
    full = _safe_resolve(rel_path, root)
    full.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write via tempfile + os.replace
    fd, tmp = tempfile.mkstemp(dir=full.parent, prefix=".nexus_tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, full)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _post_write_hooks(rel_path, content)
    try:
        from . import vault_history
        vault_history.record([rel_path], f"write: {rel_path}")
    except Exception:
        import logging
        logging.getLogger(__name__).warning("vault_history: record failed", exc_info=True)


def _post_write_hooks(rel_path: str, content: str) -> None:
    """Run search/index/graph hooks for a successful text write of rel_path.

    Extracted from write_file() so other code paths (vault_history.undo)
    can re-run indexing after restoring an older revision without duplicating
    the long catalog of best-effort calls.
    """
    try:
        from . import vault_search
        vault_search.index_path(rel_path, content)
    except Exception:
        import logging
        logging.getLogger(__name__).warning("vault_search: index_path failed", exc_info=True)
    try:
        from . import vault_index
        fm, body = _parse_frontmatter(content)
        vault_index.reindex_file(rel_path, body if body is not None else content, fm)
    except Exception:
        import logging
        logging.getLogger(__name__).warning("vault_index: reindex_file failed", exc_info=True)
    try:
        from . import vault_graph
        vault_graph.invalidate_cache()
    except Exception:
        pass
    try:
        from .server.event_bus import publish
        publish({"type": "vault.indexed", "path": rel_path})
    except Exception:
        pass
    try:
        from .agent.graphrag_manager import schedule_index
        schedule_index(rel_path, content)
    except Exception:
        import logging
        logging.getLogger(__name__).warning("graphrag: schedule_index failed", exc_info=True)


def _post_remove_hooks(rel_paths: list[str]) -> None:
    """Run search/index/graph hooks for one or more removed paths."""
    for rel in rel_paths:
        try:
            from . import vault_search
            vault_search.remove_path(rel)
        except Exception:
            import logging
            logging.getLogger(__name__).warning("vault_search: remove_path failed", exc_info=True)
        try:
            from . import vault_index
            vault_index.remove_file(rel)
        except Exception:
            import logging
            logging.getLogger(__name__).warning("vault_index: remove_file failed", exc_info=True)
        try:
            from .agent.graphrag_manager import remove_source
            remove_source(rel)
        except Exception:
            import logging
            logging.getLogger(__name__).warning("graphrag: remove_source failed", exc_info=True)
        try:
            from .server.event_bus import publish
            publish({"type": "vault.removed", "path": rel})
        except Exception:
            pass
    try:
        from . import vault_graph
        vault_graph.invalidate_cache()
    except Exception:
        pass


def delete(rel_path: str, recursive: bool = False) -> None:
    import shutil
    root = _vault_root()
    full = _safe_resolve(rel_path, root)
    removed_rel: list[str] = []
    if full.is_file():
        full.unlink()
        removed_rel.append(rel_path)
    elif full.is_dir():
        if recursive:
            root_real = Path(os.path.realpath(root))
            for sub in full.rglob("*"):
                if sub.is_file():
                    removed_rel.append(str(sub.relative_to(root_real)))
            shutil.rmtree(full)
        else:
            full.rmdir()  # raises if non-empty
    else:
        raise FileNotFoundError(f"no such file or directory: {rel_path!r}")
    _post_remove_hooks(removed_rel or [rel_path])
    try:
        from . import vault_history
        # delete may have removed a folder + many files; staging -A captures
        # the whole change set in one commit.
        vault_history.record([], f"delete: {rel_path}")
    except Exception:
        import logging
        logging.getLogger(__name__).warning("vault_history: record failed", exc_info=True)


def move(from_path: str, to_path: str) -> None:
    root = _vault_root()
    src = _safe_resolve(from_path, root)
    dst = _safe_resolve(to_path, root)
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    try:
        from . import vault_search
        vault_search.rename_path(from_path, to_path)
    except Exception:
        import logging
        logging.getLogger(__name__).warning("vault_search: rename_path failed", exc_info=True)
    try:
        from . import vault_index
        vault_index.rename_file(from_path, to_path)
    except Exception:
        import logging
        logging.getLogger(__name__).warning("vault_index: rename_file failed", exc_info=True)
    try:
        from . import vault_graph
        vault_graph.invalidate_cache()
    except Exception:
        pass
    # GraphRAG keys chunks by source_path, so a rename = remove(old) + index(new).
    try:
        from .agent.graphrag_manager import remove_source, schedule_index
        remove_source(from_path)
        try:
            with open(_safe_resolve(to_path, _vault_root()), "r", encoding="utf-8") as f:
                new_content = f.read()
            schedule_index(to_path, new_content)
        except (OSError, UnicodeDecodeError):
            pass
    except Exception:
        import logging
        logging.getLogger(__name__).warning("graphrag: move hook failed", exc_info=True)
    try:
        from .server.event_bus import publish
        publish({"type": "vault.removed", "path": from_path})
        publish({"type": "vault.indexed", "path": to_path})
    except Exception:
        pass
    try:
        from . import vault_history
        vault_history.record([], f"move: {from_path} -> {to_path}")
    except Exception:
        import logging
        logging.getLogger(__name__).warning("vault_history: record failed", exc_info=True)


def write_file_bytes(rel_path: str, data: bytes) -> None:
    if len(data) > _MAX_SIZE:
        raise ValueError("content exceeds 1 MiB limit")
    root = _vault_root()
    full = _safe_resolve(rel_path, root)
    full.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=full.parent, prefix=".nexus_tmp_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, full)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        from . import vault_graph
        vault_graph.invalidate_cache()
    except Exception:
        pass
    try:
        from . import vault_history
        vault_history.record([rel_path], f"write: {rel_path}")
    except Exception:
        import logging
        logging.getLogger(__name__).warning("vault_history: record failed", exc_info=True)


def create_folder(rel_path: str) -> None:
    root = _vault_root()
    full = _safe_resolve(rel_path, root)
    full.mkdir(parents=True, exist_ok=True)
