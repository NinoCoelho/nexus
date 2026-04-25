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
from typing import Any, Callable

import yaml

from . import vault

# Hook fired after a successful cross-lane move. The server registers a
# callback that auto-dispatches the destination lane's prompt (with a
# loop/depth guard) so the agent's tool-driven moves get the same auto-run
# behavior as a UI drag-drop. Kept as a module-level slot to avoid a circular
# import between vault_kanban and the server layer.
LaneChangeHook = Callable[..., None]
_lane_change_hook: LaneChangeHook | None = None


def set_lane_change_hook(fn: LaneChangeHook | None) -> None:
    """Register a callback fired by ``move_card`` after a cross-lane move.

    The callback receives kwargs: ``path``, ``card_id``, ``src_lane_id``,
    ``dst_lane_id``, ``dst_lane_prompt`` (may be None — caller decides
    whether to act).
    """
    global _lane_change_hook
    _lane_change_hook = fn

KANBAN_PLUGIN_KEY = "kanban-plugin"

_NX_LINE = re.compile(r"^\s*<!--\s*nx:([a-z][a-z0-9-]*)(?:=(.*?))?\s*-->\s*$", re.I)


CARD_STATUSES = {"running", "done", "failed"}
CARD_PRIORITIES = {"low", "med", "high", "urgent"}


@dataclass
class Card:
    id: str
    title: str
    body: str = ""
    session_id: str | None = None
    status: str | None = None  # None | "running" | "done" | "failed"
    # Metadata — all optional. Persisted as nx:<key>=value comments inside the
    # card body so hand-edited markdown round-trips cleanly.
    due: str | None = None        # ISO date "YYYY-MM-DD"
    priority: str | None = None   # one of CARD_PRIORITIES
    labels: list[str] = field(default_factory=list)
    assignees: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "title": self.title, "body": self.body}
        if self.session_id:
            out["session_id"] = self.session_id
        if self.status:
            out["status"] = self.status
        if self.due:
            out["due"] = self.due
        if self.priority:
            out["priority"] = self.priority
        if self.labels:
            out["labels"] = list(self.labels)
        if self.assignees:
            out["assignees"] = list(self.assignees)
        return out


@dataclass
class Lane:
    id: str
    title: str
    cards: list[Card] = field(default_factory=list)
    prompt: str | None = None
    model: str | None = None  # model id used when auto-dispatching this lane's prompt

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "title": self.title, "cards": [c.to_dict() for c in self.cards]}
        if self.prompt is not None:
            out["prompt"] = self.prompt
        if self.model is not None:
            out["model"] = self.model
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
        if "status" in meta and meta["status"] in CARD_STATUSES:
            card.status = meta["status"]
        if "due" in meta:
            card.due = meta["due"]
        if "priority" in meta and meta["priority"] in CARD_PRIORITIES:
            card.priority = meta["priority"]
        if "labels" in meta:
            card.labels = [s.strip() for s in meta["labels"].split(",") if s.strip()]
        if "assignees" in meta:
            card.assignees = [s.strip() for s in meta["assignees"].split(",") if s.strip()]
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
    lane_models: dict[str, str] = frontmatter.get("lane_models") or {}
    for ln in board.lanes:
        lp = lane_prompts.get(ln.id)
        if lp:
            ln.prompt = str(lp)
        lm = lane_models.get(ln.id)
        if lm:
            ln.model = str(lm)

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
    lm = {ln.id: ln.model for ln in board.lanes if ln.model}
    if lm:
        fm["lane_models"] = lm
    else:
        fm.pop("lane_models", None)
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
            if card.status:
                out.append(f"<!-- nx:status={card.status} -->")
            if card.due:
                out.append(f"<!-- nx:due={card.due} -->")
            if card.priority:
                out.append(f"<!-- nx:priority={card.priority} -->")
            if card.labels:
                out.append(f"<!-- nx:labels={','.join(card.labels)} -->")
            if card.assignees:
                out.append(f"<!-- nx:assignees={','.join(card.assignees)} -->")
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

    # Fire the lane-change hook for "card lands in this lane" symmetry: a
    # new card created directly in a prompt-bearing lane should run the
    # prompt, just like a move into that lane does. ``src_lane_id`` is empty
    # to flag a fresh card with no source lane.
    if _lane_change_hook is not None:
        try:
            _lane_change_hook(
                path=path,
                card_id=card.id,
                src_lane_id="",
                dst_lane_id=lane.id,
                dst_lane_prompt=lane.prompt,
            )
        except Exception:
            import logging
            logging.getLogger(__name__).exception("lane_change_hook raised on add_card")
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
    if "status" in updates:
        raw = updates["status"]
        if raw is None or raw == "":
            card.status = None
        elif raw in CARD_STATUSES:
            card.status = raw
        else:
            raise ValueError(f"invalid status {raw!r}; allowed: {sorted(CARD_STATUSES)}")
    if "due" in updates:
        raw = updates["due"]
        card.due = str(raw).strip() if raw else None
    if "priority" in updates:
        raw = updates["priority"]
        if raw is None or raw == "":
            card.priority = None
        elif raw in CARD_PRIORITIES:
            card.priority = raw
        else:
            raise ValueError(f"invalid priority {raw!r}; allowed: {sorted(CARD_PRIORITIES)}")
    if "labels" in updates:
        raw = updates["labels"]
        card.labels = _coerce_str_list(raw)
    if "assignees" in updates:
        raw = updates["assignees"]
        card.assignees = _coerce_str_list(raw)
    write_board(path, board)
    return card


def _coerce_str_list(raw: Any) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if str(s).strip()]
    if isinstance(raw, str):
        return [s.strip() for s in raw.split(",") if s.strip()]
    return []


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

    # Fire the lane-change hook *after* persisting. Cross-lane only — staying
    # within the same lane is just a reorder, never an auto-dispatch trigger.
    if _lane_change_hook is not None and src_lane.id != dst_lane.id:
        try:
            _lane_change_hook(
                path=path,
                card_id=card.id,
                src_lane_id=src_lane.id,
                dst_lane_id=dst_lane.id,
                dst_lane_prompt=dst_lane.prompt,
            )
        except Exception:
            # Never let a misbehaving hook break a successful move.
            import logging
            logging.getLogger(__name__).exception("lane_change_hook raised")
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
    if "model" in updates:
        raw = updates["model"]
        lane.model = str(raw).strip() if raw else None
    write_board(path, board)
    return lane


# ── Cross-board query ───────────────────────────────────────────────────────


def query_boards(
    *,
    text: str | None = None,
    label: str | None = None,
    assignee: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    due_before: str | None = None,
    due_after: str | None = None,
    lane: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Search every kanban board in the vault for cards matching the criteria.

    Returns a flat list of hit dicts: ``{path, board_title, lane_id, lane_title,
    card_id, title, body, due, priority, labels, assignees, status, session_id}``.
    All filters are AND-combined; a missing filter matches everything. ``text``
    is case-insensitive substring match against title + body + labels.
    """
    text_q = text.lower().strip() if text else None
    hits: list[dict[str, Any]] = []
    for entry in vault.list_tree():
        if entry.type != "file":
            continue
        path = entry.path
        if not path.endswith(".md"):
            continue
        try:
            file = vault.read_file(path)
        except (FileNotFoundError, OSError):
            continue
        if not is_kanban_file(file["content"]):
            continue
        try:
            board = parse(file["content"])
        except Exception:
            continue
        for ln in board.lanes:
            if lane and ln.id != lane and ln.title != lane:
                continue
            for card in ln.cards:
                if status and card.status != status:
                    continue
                if priority and card.priority != priority:
                    continue
                if label and label not in card.labels:
                    continue
                if assignee and assignee not in card.assignees:
                    continue
                if due_before and (not card.due or card.due > due_before):
                    continue
                if due_after and (not card.due or card.due < due_after):
                    continue
                if text_q:
                    haystack = " ".join([
                        card.title or "",
                        card.body or "",
                        ",".join(card.labels),
                        ",".join(card.assignees),
                    ]).lower()
                    if text_q not in haystack:
                        continue
                hits.append({
                    "path": path,
                    "board_title": board.title,
                    "lane_id": ln.id,
                    "lane_title": ln.title,
                    "card_id": card.id,
                    "title": card.title,
                    "body": card.body,
                    "due": card.due,
                    "priority": card.priority,
                    "labels": list(card.labels),
                    "assignees": list(card.assignees),
                    "status": card.status,
                    "session_id": card.session_id,
                })
                if len(hits) >= limit:
                    return hits
    return hits
