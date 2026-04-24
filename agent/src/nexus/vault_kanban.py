"""Vault-native kanban — Obsidian Kanban plugin format, single .md file.

Format
------
A kanban file has YAML frontmatter with ``kanban-plugin: basic`` and markdown
body organized as:

    ---
    kanban-plugin: basic
    ---

    # Board title

    ## Lane title
    <!-- nx:lane-id=<id> -->   (optional)

    ### Card title
    <!-- nx:id=<uuid> -->
    <!-- nx:session=<sid> -->   (optional, linked chat session)

    optional card body / notes

    ### Another card
    <!-- nx:id=<uuid> -->

    ## Another lane

Cards also parse from GFM task-list syntax (``- [ ] title``) for Obsidian
plugin compatibility, but we always serialize as ``###`` headings with
``<!-- nx:id=... -->`` so IDs survive reloads.

The plain-markdown file remains editable by hand and renders sensibly in any
markdown viewer — the ``<!-- nx:* -->`` comments are invisible.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import yaml

from . import vault

KANBAN_PLUGIN_KEY = "kanban-plugin"

_NX_LINE = re.compile(r"^\s*<!--\s*nx:([a-z][a-z0-9-]*)(?:=(.*?))?\s*-->\s*$", re.I)


@dataclass
class Card:
    id: str
    title: str
    body: str = ""
    session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "title": self.title, "body": self.body}
        if self.session_id:
            out["session_id"] = self.session_id
        return out


@dataclass
class Lane:
    id: str
    title: str
    cards: list[Card] = field(default_factory=list)
    prompt: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "title": self.title, "cards": [c.to_dict() for c in self.cards]}
        if self.prompt is not None:
            out["prompt"] = self.prompt
        return out


@dataclass
class Board:
    title: str
    lanes: list[Lane] = field(default_factory=list)
    frontmatter: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "lanes": [ln.to_dict() for ln in self.lanes],
        }


def is_kanban_file(content: str) -> bool:
    """Return True if the file's frontmatter declares it a kanban board."""
    if not content.startswith("---"):
        return False
    end = content.find("\n---", 3)
    if end == -1:
        return False
    try:
        fm = yaml.safe_load(content[3:end]) or {}
    except yaml.YAMLError:
        return False
    return isinstance(fm, dict) and KANBAN_PLUGIN_KEY in fm


def _slug(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    s = re.sub(r"[\s_-]+", "-", s)
    return s or "lane"


def _parse_nx(line: str) -> tuple[str, str | None] | None:
    m = _NX_LINE.match(line)
    if not m:
        return None
    return m.group(1).lower(), (m.group(2).strip() if m.group(2) is not None else None)


def _extract_card_meta(body_lines: list[str]) -> tuple[dict[str, str], str]:
    """Return ({key: value}, body_without_nx_lines)."""
    meta: dict[str, str] = {}
    kept: list[str] = []
    for line in body_lines:
        parsed = _parse_nx(line)
        if parsed is None:
            kept.append(line)
            continue
        key, value = parsed
        if value is not None:
            meta[key] = value
    # Trim leading/trailing blank lines
    while kept and not kept[0].strip():
        kept.pop(0)
    while kept and not kept[-1].strip():
        kept.pop()
    return meta, "\n".join(kept)


def parse(content: str) -> Board:
    """Parse markdown content into a Board."""
    frontmatter: dict[str, Any] = {}
    body = content
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            try:
                parsed = yaml.safe_load(content[3:end]) or {}
                if isinstance(parsed, dict):
                    frontmatter = parsed
            except yaml.YAMLError:
                pass
            body = content[end + 4:].lstrip("\n")

    board = Board(title="Kanban", frontmatter=frontmatter)
    lane: Lane | None = None
    card: Card | None = None
    card_lines: list[str] = []
    lane_uses_headers = False

    def flush_card() -> None:
        nonlocal card, card_lines
        if card is None or lane is None:
            card_lines = []
            return
        meta, cleaned = _extract_card_meta(card_lines)
        card.body = cleaned
        if "id" in meta:
            card.id = meta["id"]
        if "session" in meta:
            card.session_id = meta["session"]
        lane.cards.append(card)
        card = None
        card_lines = []

    for raw_line in body.split("\n"):
        line = raw_line
        stripped = line.strip()
        if stripped.startswith("%%"):
            # Obsidian comment block — skip (we don't track toggle state since
            # we treat any %% line as a no-op marker; good enough for parsing)
            continue
        if re.match(r"^# ", line):
            board.title = line[2:].strip()
            continue
        if line.startswith("## "):
            flush_card()
            if lane is not None:
                board.lanes.append(lane)
            title = line[3:].strip()
            lane = Lane(id=_slug(title), title=title)
            lane_uses_headers = False
            continue
        if lane is None:
            continue
        if line.startswith("### "):
            flush_card()
            lane_uses_headers = True
            card = Card(id="__pending__", title=line[4:].strip())
            continue
        if re.match(r"^- ", line) and not lane_uses_headers and card is None:
            flush_card()
            title = re.sub(r"^- (\[[ xX]\] )?", "", line).strip()
            card = Card(id="__pending__", title=title)
            continue
        if card is not None:
            card_lines.append(line)

    flush_card()
    if lane is not None:
        board.lanes.append(lane)

    lane_prompts: dict[str, str] = frontmatter.get("lane_prompts") or {}
    for ln in board.lanes:
        lp = lane_prompts.get(ln.id)
        if lp:
            ln.prompt = str(lp)

    return board


def _ensure_id(card: Card) -> str:
    if not card.id or card.id == "__pending__":
        card.id = str(uuid.uuid4())
    return card.id


def serialize(board: Board) -> str:
    """Serialize a Board back into markdown."""
    fm = dict(board.frontmatter)
    fm.setdefault(KANBAN_PLUGIN_KEY, "basic")
    lp = {ln.id: ln.prompt for ln in board.lanes if ln.prompt}
    if lp:
        fm["lane_prompts"] = lp
    else:
        fm.pop("lane_prompts", None)
    fm_text = yaml.dump(fm, default_flow_style=False, sort_keys=False).rstrip()
    out = [f"---\n{fm_text}\n---", "", f"# {board.title}", ""]
    for lane in board.lanes:
        out.append(f"## {lane.title}")
        out.append("")
        for card in lane.cards:
            out.append(f"### {card.title}")
            out.append(f"<!-- nx:id={_ensure_id(card)} -->")
            if card.session_id:
                out.append(f"<!-- nx:session={card.session_id} -->")
            body = (card.body or "").strip()
            if body:
                out.append("")
                out.append(body)
            out.append("")
    return "\n".join(out).rstrip() + "\n"


# ── High-level operations on vault paths ────────────────────────────────────


def read_board(path: str) -> Board:
    file = vault.read_file(path)
    return parse(file["content"])


def write_board(path: str, board: Board) -> None:
    vault.write_file(path, serialize(board))


def create_empty(path: str, title: str | None = None, columns: list[str] | None = None) -> Board:
    """Scaffold a new kanban file at path with default lanes."""
    cols = columns or ["Todo", "Doing", "Done"]
    board = Board(
        title=title or path.rsplit("/", 1)[-1].removesuffix(".md").replace("-", " ").title() or "Kanban",
        frontmatter={KANBAN_PLUGIN_KEY: "basic"},
        lanes=[Lane(id=_slug(c), title=c) for c in cols],
    )
    write_board(path, board)
    return board


def _find_card(board: Board, card_id: str) -> tuple[Lane, Card, int] | None:
    for lane in board.lanes:
        for idx, card in enumerate(lane.cards):
            if card.id == card_id:
                return lane, card, idx
    return None


def _find_lane(board: Board, lane_id: str) -> Lane | None:
    for lane in board.lanes:
        if lane.id == lane_id:
            return lane
    return None


def add_card(
    path: str,
    lane_id: str,
    title: str,
    body: str = "",
) -> Card:
    board = read_board(path)
    lane = _find_lane(board, lane_id)
    if lane is None:
        raise KeyError(f"lane {lane_id!r} not found")
    card = Card(id=str(uuid.uuid4()), title=title, body=body)
    lane.cards.append(card)
    write_board(path, board)
    return card


def update_card(
    path: str,
    card_id: str,
    updates: dict[str, Any],
) -> Card:
    board = read_board(path)
    found = _find_card(board, card_id)
    if found is None:
        raise KeyError(f"card {card_id!r} not found")
    _, card, _ = found
    if "title" in updates:
        card.title = str(updates["title"])
    if "body" in updates:
        card.body = str(updates["body"])
    if "session_id" in updates:
        sid = updates["session_id"]
        card.session_id = str(sid) if sid else None
    write_board(path, board)
    return card


def move_card(
    path: str,
    card_id: str,
    lane_id: str,
    position: int | None = None,
) -> Card:
    board = read_board(path)
    found = _find_card(board, card_id)
    if found is None:
        raise KeyError(f"card {card_id!r} not found")
    src_lane, card, src_idx = found
    dst_lane = _find_lane(board, lane_id)
    if dst_lane is None:
        raise KeyError(f"lane {lane_id!r} not found")
    src_lane.cards.pop(src_idx)
    if position is None or position >= len(dst_lane.cards):
        dst_lane.cards.append(card)
    else:
        dst_lane.cards.insert(max(0, position), card)
    write_board(path, board)
    return card


def delete_card(path: str, card_id: str) -> None:
    board = read_board(path)
    found = _find_card(board, card_id)
    if found is None:
        raise KeyError(f"card {card_id!r} not found")
    lane, _, idx = found
    lane.cards.pop(idx)
    write_board(path, board)


def add_lane(path: str, title: str) -> Lane:
    board = read_board(path)
    lane = Lane(id=_slug(title), title=title)
    # Ensure lane id is unique
    existing = {l.id for l in board.lanes}
    if lane.id in existing:
        base = lane.id
        i = 2
        while f"{base}-{i}" in existing:
            i += 1
        lane.id = f"{base}-{i}"
    board.lanes.append(lane)
    write_board(path, board)
    return lane


def delete_lane(path: str, lane_id: str) -> None:
    board = read_board(path)
    board.lanes = [l for l in board.lanes if l.id != lane_id]
    write_board(path, board)


def update_lane(path: str, lane_id: str, updates: dict[str, Any]) -> Lane:
    board = read_board(path)
    lane = _find_lane(board, lane_id)
    if lane is None:
        raise KeyError(f"lane {lane_id!r} not found")
    if "title" in updates:
        lane.title = str(updates["title"])
    if "prompt" in updates:
        raw = updates["prompt"]
        lane.prompt = str(raw).strip() if raw else None
    write_board(path, board)
    return lane
