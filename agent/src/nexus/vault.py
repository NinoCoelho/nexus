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


def read_file(rel_path: str) -> dict[str, Any]:
    root = _vault_root()
    full = _safe_resolve(rel_path, root)
    if not full.is_file():
        raise FileNotFoundError(f"no such file: {rel_path!r}")
    content = full.read_text(encoding="utf-8")
    result: dict[str, Any] = {"path": rel_path, "content": content}
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
    for rel in removed_rel or [rel_path]:
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
        from . import vault_graph
        vault_graph.invalidate_cache()
    except Exception:
        pass


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


def create_folder(rel_path: str) -> None:
    root = _vault_root()
    full = _safe_resolve(rel_path, root)
    full.mkdir(parents=True, exist_ok=True)
