"""High-level calendar I/O, listing, and cross-calendar query."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .. import vault
from .models import CALENDAR_PLUGIN_KEY, Calendar, is_calendar_file
from .parser import parse, serialize
from .recurrence import _parse_iso, expand_window


@dataclass
class CalendarSummary:
    path: str
    title: str
    timezone: str
    auto_trigger: bool
    event_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "title": self.title,
            "timezone": self.timezone,
            "auto_trigger": self.auto_trigger,
            "event_count": self.event_count,
        }


def read_calendar(path: str) -> Calendar:
    file = vault.read_file(path)
    return parse(file["content"])


def write_calendar(path: str, cal: Calendar) -> None:
    vault.write_file(path, serialize(cal))


def create_empty(
    path: str,
    title: str | None = None,
    timezone: str | None = None,
    prompt: str | None = None,
) -> Calendar:
    """Scaffold a new calendar .md file at path."""
    fm: dict[str, Any] = {CALENDAR_PLUGIN_KEY: "basic"}
    if prompt:
        fm["calendar_prompt"] = prompt
    if timezone:
        fm["timezone"] = timezone
    fm.setdefault("auto_trigger", True)
    fm.setdefault("default_duration_min", 30)
    cal = Calendar(
        title=title or path.rsplit("/", 1)[-1].removesuffix(".md").replace("-", " ").title() or "Calendar",
        frontmatter=fm,
    )
    write_calendar(path, cal)
    return cal


def update_calendar(path: str, updates: dict[str, Any]) -> Calendar:
    """Patch calendar-level metadata (title, timezone, prompt, auto_trigger).

    ``updates`` keys recognised: ``title``, ``timezone``, ``prompt`` (maps to
    frontmatter ``calendar_prompt``), ``auto_trigger``, ``default_duration_min``.
    Unknown keys are ignored.
    """
    cal = read_calendar(path)
    if "title" in updates:
        cal.title = str(updates["title"])
    if "timezone" in updates:
        cal.frontmatter["timezone"] = str(updates["timezone"])
    if "prompt" in updates:
        prompt = updates["prompt"]
        if prompt:
            cal.frontmatter["calendar_prompt"] = str(prompt)
        else:
            cal.frontmatter.pop("calendar_prompt", None)
    if "auto_trigger" in updates:
        cal.frontmatter["auto_trigger"] = bool(updates["auto_trigger"])
    if "default_duration_min" in updates:
        try:
            cal.frontmatter["default_duration_min"] = int(updates["default_duration_min"])
        except (TypeError, ValueError):
            pass
    write_calendar(path, cal)
    return cal


def list_calendars() -> list[CalendarSummary]:
    """Walk the vault tree and summarise every calendar file."""
    out: list[CalendarSummary] = []
    for entry in vault.list_tree():
        if entry.type != "file":
            continue
        if not entry.path.endswith(".md"):
            continue
        try:
            file = vault.read_file(entry.path)
        except (FileNotFoundError, OSError):
            continue
        if not is_calendar_file(file["content"]):
            continue
        try:
            cal = parse(file["content"])
        except Exception:
            continue
        out.append(
            CalendarSummary(
                path=entry.path,
                title=cal.title,
                timezone=cal.timezone,
                auto_trigger=cal.auto_trigger,
                event_count=len(cal.events),
            )
        )
    out.sort(key=lambda s: s.path.lower())
    return out


def query_events(
    *,
    from_utc: str | None = None,
    to_utc: str | None = None,
    status: str | None = None,
    calendar_path: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Search every calendar for events in [from_utc, to_utc] matching status.

    Recurring events are expanded inside the window. Returns flat hit dicts.
    """
    win_from = _parse_iso(from_utc) if from_utc else None
    win_to = _parse_iso(to_utc) if to_utc else None
    paths: list[str]
    if calendar_path:
        paths = [calendar_path]
    else:
        paths = [s.path for s in list_calendars()]
    hits: list[dict[str, Any]] = []
    for path in paths:
        try:
            cal = read_calendar(path)
        except (FileNotFoundError, OSError, ValueError):
            continue
        tz = cal.timezone
        for ev in cal.events:
            if status and ev.status != status:
                continue
            occurrences: list[datetime]
            if win_from and win_to:
                occurrences = expand_window(ev, win_from, win_to, tz=tz)
            else:
                parsed = _parse_iso(ev.start)
                occurrences = [parsed] if parsed else []
            for occ in occurrences:
                hit = ev.to_dict()
                hit["path"] = path
                hit["calendar_title"] = cal.title
                hit["occurrence_start"] = occ.strftime("%Y-%m-%dT%H:%M:%SZ")
                hits.append(hit)
                if len(hits) >= limit:
                    return hits
    return hits
