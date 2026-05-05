"""Dataclasses for kanban entities and the is_kanban_file predicate."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

KANBAN_PLUGIN_KEY = "kanban-plugin"

CARD_STATUSES = {"running", "done", "failed"}
CARD_PRIORITIES = {"low", "med", "high", "urgent"}


@dataclass
class Card:
    id: str
    title: str
    body: str = ""
    session_id: str | None = None
    status: str | None = None  # None | "running" | "done" | "failed"
    checked: bool = False  # GFM task checkbox state — independent of run-status
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
        if self.checked:
            out["checked"] = True
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
    model: str | None = None
    webhook_token: str | None = None
    webhook_enabled: bool = False

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
