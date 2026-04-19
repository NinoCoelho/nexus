"""Agentic kanban — markdown+frontmatter cards under ~/.nexus/vault/boards/<board>/."""

from __future__ import annotations

import logging
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .vault import _vault_root, _safe_resolve

log = logging.getLogger(__name__)

_DEFAULT_COLUMNS = ["todo", "doing", "done"]
_BOARD_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")

BOARDS_ROOT = Path.home() / ".nexus" / "vault" / "boards"


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


def _migrate_legacy() -> None:
    """If legacy vault/kanban/ exists and boards/ doesn't, migrate it to boards/default/."""
    legacy = _vault_root() / "kanban"
    if legacy.is_dir() and not BOARDS_ROOT.exists():
        BOARDS_ROOT.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy), str(BOARDS_ROOT / "default"))
        log.info("Migrated legacy ~/.nexus/vault/kanban/ to ~/.nexus/vault/boards/default/")


def _board_root(board: str = "default") -> Path:
    """Return and ensure the root directory for the given board."""
    _migrate_legacy()
    if not _BOARD_NAME_RE.match(board):
        raise ValueError(f"invalid board name {board!r} — must match ^[a-z0-9][a-z0-9-]{{0,31}}$")
    root = BOARDS_ROOT / board
    root.mkdir(parents=True, exist_ok=True)
    for col in _DEFAULT_COLUMNS:
        (root / col).mkdir(exist_ok=True)
    return root


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slugify(title: str) -> str:
    import re as _re
    s = title.lower().strip()
    s = _re.sub(r"[^\w\s-]", "", s)
    s = _re.sub(r"[\s_-]+", "-", s)
    return s[:40] or "card"


def _card_path(board_root: Path, column: str, card_id: str, slug: str) -> Path:
    return board_root / column / f"{slug}-{card_id[:8]}.md"


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


# ── Board management ───────────────────────────────────────────────────────────

def list_boards() -> list[dict[str, Any]]:
    """Return [{name, card_count}] for all boards. Seeds/migrates on first call."""
    _migrate_legacy()
    if not BOARDS_ROOT.exists():
        # First-ever call: seed the default board
        _board_root("default")
    boards = []
    for d in sorted(BOARDS_ROOT.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        count = sum(1 for col in d.iterdir() if col.is_dir() for _ in col.glob("*.md"))
        boards.append({"name": d.name, "card_count": count})
    return boards


def create_board(name: str) -> None:
    if not _BOARD_NAME_RE.match(name):
        raise ValueError(f"invalid board name {name!r} — must match ^[a-z0-9][a-z0-9-]{{0,31}}$")
    _migrate_legacy()
    board_dir = BOARDS_ROOT / name
    if board_dir.exists():
        raise ValueError(f"board {name!r} already exists")
    board_dir.mkdir(parents=True, exist_ok=True)
    for col in _DEFAULT_COLUMNS:
        (board_dir / col).mkdir()


def delete_board(name: str) -> None:
    _migrate_legacy()
    board_dir = BOARDS_ROOT / name
    if not board_dir.is_dir():
        raise KeyError(f"board {name!r} not found")
    # Must not be the last board
    all_boards = [d for d in BOARDS_ROOT.iterdir() if d.is_dir() and not d.name.startswith(".")]
    if len(all_boards) <= 1:
        raise ValueError(f"cannot delete the last remaining board")
    # Must have zero cards
    card_count = sum(1 for col in board_dir.iterdir() if col.is_dir() for _ in col.glob("*.md"))
    if card_count > 0:
        raise ValueError(f"board {name!r} still has {card_count} card(s) — delete them first")
    shutil.rmtree(board_dir)


# ── Column / card operations ───────────────────────────────────────────────────

def list_columns(board: str = "default") -> list[str]:
    root = _board_root(board)
    cols = sorted(d.name for d in root.iterdir() if d.is_dir() and not d.name.startswith("."))
    return cols


def list_cards(board: str = "default") -> list[Card]:
    root = _board_root(board)
    cards: list[Card] = []
    for col_dir in sorted(root.iterdir()):
        if not col_dir.is_dir() or col_dir.name.startswith("."):
            continue
        for md in sorted(col_dir.glob("*.md")):
            card = _parse_card_file(md)
            if card:
                cards.append(card)
    return cards


def _find_card(card_id: str, board: str = "default") -> tuple[Path, Card] | None:
    root = _board_root(board)
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
    board: str = "default",
) -> Card:
    root = _board_root(board)
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


def move_card(card_id: str, column: str, board: str = "default") -> Card:
    found = _find_card(card_id, board)
    if not found:
        raise KeyError(f"card {card_id!r} not found")
    old_path, card = found
    root = _board_root(board)
    col_dir = root / column
    col_dir.mkdir(exist_ok=True)
    card.column = column
    card.updated_at = _now_iso()
    slug = _slugify(card.title)
    new_path = _card_path(root, column, card_id, slug)
    new_path.write_text(_render_card(card), encoding="utf-8")
    old_path.unlink()
    return card


def update_card(card_id: str, updates: dict[str, Any], board: str = "default") -> Card:
    found = _find_card(card_id, board)
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
        return move_card(card_id, updates["column"], board)
    path.write_text(_render_card(card), encoding="utf-8")
    return card


def delete_card(card_id: str, board: str = "default") -> None:
    found = _find_card(card_id, board)
    if not found:
        raise KeyError(f"card {card_id!r} not found")
    path, _ = found
    path.unlink()


def create_column(name: str, board: str = "default") -> None:
    root = _board_root(board)
    (root / name).mkdir(exist_ok=True)


def delete_column(name: str, board: str = "default") -> None:
    root = _board_root(board)
    col_dir = root / name
    if not col_dir.is_dir():
        raise KeyError(f"column {name!r} not found")
    cards = list(col_dir.glob("*.md"))
    if cards:
        raise ValueError(f"column {name!r} is not empty")
    col_dir.rmdir()
