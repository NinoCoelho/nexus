"""Persistent UI state: which folder graphs are currently pinned as tabs.

Stored in ``~/.nexus/folder_graphs.json``. Source of truth for graph data
remains the per-folder ``.nexus-graph/`` directory; this file is only the
list of folders the user wants the UI to remember between sessions.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_FILENAME = "folder_graphs.json"


def _state_file() -> Path:
    from ..graphrag_manager import get_home
    return get_home() / _FILENAME


def _norm_tab_path(path: str) -> str:
    """Normalise a vault-relative tab path for storage / dedup.

    Strips leading/trailing slashes and collapses repeated ``/`` so that
    ``/foo/bar`` and ``foo/bar/`` compare equal.  Does **not** call
    ``os.path.realpath`` — the path is vault-relative, not filesystem-relative.
    """
    return "/".join(part for part in path.strip().split("/") if part)


def list_tabs() -> list[dict[str, Any]]:
    """Return the saved tabs list, or [] if missing/corrupt.

    Each tab is ``{"path": str, "label": str}`` with the folder's basename
    as the default label.
    """
    p = _state_file()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("[folder_graph] failed to read %s", p, exc_info=True)
        return []
    tabs = data.get("open_tabs") if isinstance(data, dict) else data
    if not isinstance(tabs, list):
        return []
    out: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for entry in tabs:
        if not isinstance(entry, dict):
            continue
        path = _norm_tab_path(str(entry.get("path") or ""))
        if not path or path in seen_paths:
            continue
        seen_paths.add(path)
        label = str(entry.get("label") or Path(path).name or path)
        out.append({"path": path, "label": label})
    return out


def set_tabs(tabs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace the tabs list. Returns the cleaned/normalised list written."""
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in tabs:
        if not isinstance(entry, dict):
            continue
        path = _norm_tab_path(str(entry.get("path") or ""))
        if not path:
            continue
        if path in seen:
            continue
        seen.add(path)
        label = str(entry.get("label") or "").strip() or Path(path).name or path
        cleaned.append({"path": path, "label": label})

    p = _state_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"open_tabs": cleaned}, indent=2), encoding="utf-8")
    return cleaned


def add_tab(path: str, label: str | None = None) -> list[dict[str, Any]]:
    """Idempotent: appends if missing, no-op if already present."""
    norm = _norm_tab_path(path)
    current = list_tabs()
    for tab in current:
        if _norm_tab_path(tab["path"]) == norm:
            return current
    current.append({"path": norm, "label": label or Path(norm).name or norm})
    return set_tabs(current)


def remove_tab(path: str) -> list[dict[str, Any]]:
    norm = _norm_tab_path(path)
    current = list_tabs()
    kept = [t for t in current if _norm_tab_path(t["path"]) != norm]
    if len(kept) == len(current):
        return current
    return set_tabs(kept)
