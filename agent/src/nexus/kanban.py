"""Agentic kanban — markdown+frontmatter cards under ~/.nexus/vault/kanban/."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .vault import _vault_root, _safe_resolve

_DEFAULT_COLUMNS = ["todo", "doing", "done"]


@dataclass
class Card:
    id: str
    title: str
    column: str
    created_at: str
    updated_at: str
    tags: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "column": self.column,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "tags": self.tags,
            "notes": self.notes,
        }


def _kanban_root() -> Path:
    root = _vault_root() / "kanban"
    root.mkdir(parents=True, exist_ok=True)
    # Seed default columns
    for col in _DEFAULT_COLUMNS:
        (root / col).mkdir(exist_ok=True)
    return root


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slugify(title: str) -> str:
    import re
    s = title.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:40] or "card"


def _card_path(kanban_root: Path, column: str, card_id: str, slug: str) -> Path:
    return kanban_root / column / f"{slug}-{card_id[:8]}.md"


def _render_card(card: Card) -> str:
    fm: dict[str, Any] = {
        "id": card.id,
        "title": card.title,
        "column": card.column,
        "created_at": card.created_at,
        "updated_at": card.updated_at,
        "tags": card.tags,
    }
    return f"---\n{yaml.dump(fm, default_flow_style=False).rstrip()}\n---\n\n{card.notes}"


def _parse_card_file(path: Path) -> Card | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    fm_text = text[3:end].strip()
    notes = text[end + 4:].lstrip("\n")
    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError:
        return None
    if not isinstance(fm, dict):
        return None
    return Card(
        id=fm.get("id", ""),
        title=fm.get("title", ""),
        column=fm.get("column", path.parent.name),
        created_at=fm.get("created_at", ""),
        updated_at=fm.get("updated_at", ""),
        tags=fm.get("tags") or [],
        notes=notes,
    )


def list_columns() -> list[str]:
    root = _kanban_root()
    cols = sorted(d.name for d in root.iterdir() if d.is_dir() and not d.name.startswith("."))
    return cols


def list_cards() -> list[Card]:
    root = _kanban_root()
    cards: list[Card] = []
    for col_dir in sorted(root.iterdir()):
        if not col_dir.is_dir() or col_dir.name.startswith("."):
            continue
        for md in sorted(col_dir.glob("*.md")):
            card = _parse_card_file(md)
            if card:
                cards.append(card)
    return cards


def _find_card(card_id: str) -> tuple[Path, Card] | None:
    root = _kanban_root()
    for col_dir in root.iterdir():
        if not col_dir.is_dir():
            continue
        for md in col_dir.glob("*.md"):
            card = _parse_card_file(md)
            if card and card.id == card_id:
                return md, card
    return None


def create_card(
    title: str,
    column: str = "todo",
    notes: str = "",
    tags: list[str] | None = None,
) -> Card:
    root = _kanban_root()
    col_dir = root / column
    col_dir.mkdir(exist_ok=True)
    now = _now_iso()
    card_id = str(uuid.uuid4())
    card = Card(
        id=card_id,
        title=title,
        column=column,
        created_at=now,
        updated_at=now,
        tags=tags or [],
        notes=notes,
    )
    slug = _slugify(title)
    path = _card_path(root, column, card_id, slug)
    path.write_text(_render_card(card), encoding="utf-8")
    return card


def move_card(card_id: str, column: str) -> Card:
    found = _find_card(card_id)
    if not found:
        raise KeyError(f"card {card_id!r} not found")
    old_path, card = found
    root = _kanban_root()
    col_dir = root / column
    col_dir.mkdir(exist_ok=True)
    card.column = column
    card.updated_at = _now_iso()
    slug = _slugify(card.title)
    new_path = _card_path(root, column, card_id, slug)
    new_path.write_text(_render_card(card), encoding="utf-8")
    old_path.unlink()
    return card


def update_card(card_id: str, updates: dict[str, Any]) -> Card:
    found = _find_card(card_id)
    if not found:
        raise KeyError(f"card {card_id!r} not found")
    path, card = found
    column_changed = "column" in updates and updates["column"] != card.column
    if "title" in updates:
        card.title = updates["title"]
    if "notes" in updates:
        card.notes = updates["notes"]
    if "tags" in updates:
        card.tags = updates["tags"]
    card.updated_at = _now_iso()
    if column_changed:
        return move_card(card_id, updates["column"])
    path.write_text(_render_card(card), encoding="utf-8")
    return card


def delete_card(card_id: str) -> None:
    found = _find_card(card_id)
    if not found:
        raise KeyError(f"card {card_id!r} not found")
    path, _ = found
    path.unlink()


def create_column(name: str) -> None:
    root = _kanban_root()
    (root / name).mkdir(exist_ok=True)


def delete_column(name: str) -> None:
    root = _kanban_root()
    col_dir = root / name
    if not col_dir.is_dir():
        raise KeyError(f"column {name!r} not found")
    cards = list(col_dir.glob("*.md"))
    if cards:
        raise ValueError(f"column {name!r} is not empty")
    col_dir.rmdir()
