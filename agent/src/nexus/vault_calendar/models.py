"""Dataclasses for calendar entities and the is_calendar_file predicate."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

CALENDAR_PLUGIN_KEY = "calendar-plugin"

# Status owned by: scheduled (initial) → triggered (driver fired) →
# done/failed (background turn finished); missed (catch-up); cancelled (user).
EVENT_STATUSES = {"scheduled", "triggered", "done", "failed", "missed", "cancelled"}

# trigger="on_start" means the heartbeat driver fires the event when its start
# time arrives. trigger="off" suppresses auto-firing for this specific event,
# overriding the calendar-level auto_trigger default. Absent → inherit.
EVENT_TRIGGERS = {"on_start", "off"}

# Sentinel value of ``Event.assignee`` that opts an event into agent dispatch.
# Any other (or missing) value means the event is a plain calendar entry —
# the heartbeat driver will not fire it.
ASSIGNEE_AGENT = "agent"


@dataclass
class Event:
    id: str
    title: str
    start: str  # ISO-8601 UTC ("...Z") or "YYYY-MM-DD" for all-day
    end: str | None = None  # same shape as start; optional
    body: str = ""
    status: str = "scheduled"
    trigger: str | None = None  # None=inherit; "on_start" | "off"
    rrule: str | None = None  # iCal RRULE string (e.g. "FREQ=WEEKLY;BYDAY=MO")
    session_id: str | None = None
    all_day: bool = False
    # Fire-window fields — only meaningful for all_day events. When set, the
    # heartbeat driver fires the event repeatedly during the window
    # [fire_from, fire_to] in the calendar's local timezone, every
    # fire_every_min minutes. Useful for "during business hours, check news
    # every 30 min" rotines without modelling each tick as its own event.
    fire_from: str | None = None  # "HH:MM" local time
    fire_to: str | None = None  # "HH:MM" local time
    fire_every_min: int | None = None
    # Specific model id to use when the agent runs this event. Falls back to
    # the calendar's ``default_model``, then the agent's configured default.
    model: str | None = None
    # Who/what runs this event. ``"agent"`` opts in to heartbeat-driven
    # dispatch; anything else (including None) makes it a plain calendar
    # entry that never fires. Free-form values are reserved for future
    # human-attendee tracking.
    assignee: str | None = None
    # Per-occurrence completion log for recurring events. Stores UTC ISO
    # ``occurrence_start`` strings. Lets one instance of a recurring event
    # be marked done without flipping the parent ``status`` (which would
    # cascade to every other expanded occurrence). Also gates the driver:
    # an entry here means "skip this occurrence." Always empty for
    # non-recurring events.
    completed_occurrences: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "start": self.start,
            "status": self.status,
            "all_day": self.all_day,
        }
        if self.end:
            out["end"] = self.end
        if self.body:
            out["body"] = self.body
        if self.trigger is not None:
            out["trigger"] = self.trigger
        if self.rrule:
            out["rrule"] = self.rrule
        if self.session_id:
            out["session_id"] = self.session_id
        if self.fire_from:
            out["fire_from"] = self.fire_from
        if self.fire_to:
            out["fire_to"] = self.fire_to
        if self.fire_every_min:
            out["fire_every_min"] = self.fire_every_min
        if self.model:
            out["model"] = self.model
        if self.assignee:
            out["assignee"] = self.assignee
        if self.completed_occurrences:
            out["completed_occurrences"] = list(self.completed_occurrences)
        return out

    @property
    def is_agent_assigned(self) -> bool:
        return self.assignee == ASSIGNEE_AGENT

    @property
    def has_fire_window(self) -> bool:
        return bool(self.all_day and self.fire_from and self.fire_to and self.fire_every_min)


@dataclass
class Calendar:
    title: str
    events: list[Event] = field(default_factory=list)
    frontmatter: dict[str, Any] = field(default_factory=dict)

    @property
    def calendar_prompt(self) -> str | None:
        v = self.frontmatter.get("calendar_prompt")
        return str(v) if v else None

    @property
    def timezone(self) -> str:
        v = self.frontmatter.get("timezone")
        return str(v) if v else "UTC"

    @property
    def auto_trigger(self) -> bool:
        # default: True (auto-fire events unless explicitly disabled)
        v = self.frontmatter.get("auto_trigger")
        if v is None:
            return True
        return bool(v)

    @property
    def default_duration_min(self) -> int:
        v = self.frontmatter.get("default_duration_min")
        try:
            return int(v) if v is not None else 30
        except (TypeError, ValueError):
            return 30

    @property
    def default_model(self) -> str | None:
        v = self.frontmatter.get("default_model")
        return str(v) if v else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "events": [e.to_dict() for e in self.events],
            "calendar_prompt": self.calendar_prompt,
            "timezone": self.timezone,
            "auto_trigger": self.auto_trigger,
            "default_duration_min": self.default_duration_min,
            "default_model": self.default_model,
        }


def is_calendar_file(content: str) -> bool:
    """Return True if the file's frontmatter declares it a calendar."""
    if not content.startswith("---"):
        return False
    end = content.find("\n---", 3)
    if end == -1:
        return False
    try:
        fm = yaml.safe_load(content[3:end]) or {}
    except yaml.YAMLError:
        return False
    return isinstance(fm, dict) and CALENDAR_PLUGIN_KEY in fm
