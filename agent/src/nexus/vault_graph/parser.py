"""Frontmatter parsing and path helpers for vault_graph."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _top_folder(rel: str) -> str:
    parts = Path(rel).parts
    return parts[0] if len(parts) > 1 else ""


def _parse_frontmatter(content: str) -> dict[str, Any] | None:
    if not content.startswith("---"):
        return None
    end = content.find("\n---", 3)
    if end == -1:
        return None
    fm_text = content[3:end].strip()
    try:
        fm = yaml.safe_load(fm_text)
        return fm if isinstance(fm, dict) else None
    except yaml.YAMLError:
        return None


def _extract_title(content: str) -> str:
    fm = _parse_frontmatter(content)
    if fm and isinstance(fm.get("title"), str):
        return fm["title"]
    first_line = content.lstrip().split("\n", 1)[0].strip()
    if first_line.startswith("# "):
        return first_line[2:].strip()
    return ""
