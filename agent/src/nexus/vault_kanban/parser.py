"""Parse and serialize kanban markdown files."""

from __future__ import annotations

import re
import uuid
from typing import Any

import yaml

from .models import (
    CARD_PRIORITIES,
    CARD_STATUSES,
    KANBAN_PLUGIN_KEY,
    Board,
    Card,
    Lane,
)

_NX_LINE = re.compile(r"^\s*<!--\s*nx:([a-z][a-z0-9-]*)(?:=(.*?))?\s*-->\s*$", re.I)


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


def _ensure_id(card: Card) -> str:
    if not card.id or card.id == "__pending__":
        card.id = str(uuid.uuid4())
    return card.id


_LIST_CARD = re.compile(r"^- (?:\[([ xX])\] )?(.*)$")


def _apply_card_meta(card: Card, meta: dict[str, str]) -> None:
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


def parse(content: str) -> Board:
    """Parse markdown content into a Board.

    Cards are recognized in two formats:

    * **List-style** (preferred, matches the Obsidian Kanban plugin)::

          - [ ] Title
              <!-- nx:id=... -->
              body line 1
              body line 2

      Body lines are indented with 4 spaces or a tab and de-indented on read.
      Anything inside the indented block — sub-lists, ``###`` headings,
      fenced code blocks, blockquotes — is preserved verbatim.

    * **Header-style** (legacy, kept for compatibility with older boards)::

          ### Title
          <!-- nx:id=... -->
          body line

      The body extends until the next ``###``/``##``/``#`` heading at column 0.
    """
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
    card_style = ""  # "list" or "header" — only meaningful while card is open

    def flush_card() -> None:
        nonlocal card, card_lines, card_style
        if card is None or lane is None:
            card_lines = []
            card_style = ""
            return
        meta, cleaned = _extract_card_meta(card_lines)
        card.body = cleaned
        _apply_card_meta(card, meta)
        lane.cards.append(card)
        card = None
        card_lines = []
        card_style = ""

    for raw in body.split("\n"):
        stripped = raw.strip()
        if stripped.startswith("%%"):
            # Obsidian transient comment — drop entirely (no toggle state tracked).
            continue

        # If a card is open, decide whether this line continues its body or terminates it.
        if card is not None:
            if card_style == "list":
                if raw.startswith("    "):
                    card_lines.append(raw[4:])
                    continue
                if raw.startswith("\t"):
                    card_lines.append(raw[1:])
                    continue
                if not stripped:
                    card_lines.append("")
                    continue
                # Column-0 non-blank line ends the list-style card; fall through.
                flush_card()
            else:  # "header" — legacy: body extends until the next heading
                if raw.startswith("### ") or raw.startswith("## ") or raw.startswith("# "):
                    flush_card()
                    # fall through to outer parsing
                else:
                    card_lines.append(raw)
                    continue

        # Board title
        if raw.startswith("# "):
            board.title = raw[2:].strip()
            continue

        # Lane header
        if raw.startswith("## "):
            if lane is not None:
                board.lanes.append(lane)
            title = raw[3:].strip()
            lane = Lane(id=_slug(title), title=title)
            continue

        if lane is None:
            continue

        # List-style card (preferred)
        m = _LIST_CARD.match(raw)
        if m:
            check_char = m.group(1) or " "
            title = m.group(2).strip()
            card = Card(
                id="__pending__",
                title=title,
                checked=(check_char.lower() == "x"),
            )
            card_style = "list"
            continue

        # Header-style card (legacy)
        if raw.startswith("### "):
            card = Card(id="__pending__", title=raw[4:].strip())
            card_style = "header"
            continue

        # Anything else outside a card is ignored.

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


def _check_char(card: Card) -> str:
    return "x" if (card.checked or card.status == "done") else " "


def _indent_body(body: str) -> list[str]:
    """Indent each line with 4 spaces; preserve empty lines as truly empty."""
    return [(("    " + ln) if ln else "") for ln in body.split("\n")]


def serialize(board: Board) -> str:
    """Serialize a Board back into Obsidian-Kanban-compatible markdown.

    Cards are emitted as ``- [ ] Title`` (or ``- [x]`` if checked / status="done")
    with all metadata comments and body content indented by 4 spaces, so any
    markdown inside the body — including ``##`` headings, sub-lists and code
    fences — round-trips losslessly.
    """
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
    out: list[str] = [f"---\n{fm_text}\n---", "", f"# {board.title}", ""]
    for lane in board.lanes:
        out.append(f"## {lane.title}")
        out.append("")
        for card in lane.cards:
            out.append(f"- [{_check_char(card)}] {card.title}")
            out.append(f"    <!-- nx:id={_ensure_id(card)} -->")
            if card.session_id:
                out.append(f"    <!-- nx:session={card.session_id} -->")
            if card.status:
                out.append(f"    <!-- nx:status={card.status} -->")
            if card.due:
                out.append(f"    <!-- nx:due={card.due} -->")
            if card.priority:
                out.append(f"    <!-- nx:priority={card.priority} -->")
            if card.labels:
                out.append(f"    <!-- nx:labels={','.join(card.labels)} -->")
            if card.assignees:
                out.append(f"    <!-- nx:assignees={','.join(card.assignees)} -->")
            body = (card.body or "").strip()
            if body:
                out.append("")
                out.extend(_indent_body(body))
            out.append("")
    return "\n".join(out).rstrip() + "\n"
