"""Parse and serialize calendar markdown files."""

from __future__ import annotations

import re
import uuid
from typing import Any

import yaml

from .models import (
    CALENDAR_PLUGIN_KEY,
    EVENT_STATUSES,
    EVENT_TRIGGERS,
    Calendar,
    Event,
)

_NX_LINE = re.compile(r"^\s*<!--\s*nx:([a-z][a-z0-9_-]*)(?:=(.*?))?\s*-->\s*$", re.I)


def _parse_nx(line: str) -> tuple[str, str | None] | None:
    m = _NX_LINE.match(line)
    if not m:
        return None
    return m.group(1).lower(), (m.group(2).strip() if m.group(2) is not None else None)


def _extract_event_meta(body_lines: list[str]) -> tuple[dict[str, str], str]:
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
        else:
            # Bare ``<!-- nx:all-day -->`` (no value) treated as boolean true.
            meta[key] = "1"
    while kept and not kept[0].strip():
        kept.pop(0)
    while kept and not kept[-1].strip():
        kept.pop()
    return meta, "\n".join(kept)


def _ensure_id(event: Event) -> str:
    if not event.id or event.id == "__pending__":
        event.id = str(uuid.uuid4())
    return event.id


def parse(content: str) -> Calendar:
    """Parse markdown content into a Calendar."""
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

    cal = Calendar(title="Calendar", frontmatter=frontmatter)
    event: Event | None = None
    event_lines: list[str] = []

    def flush_event() -> None:
        nonlocal event, event_lines
        if event is None:
            event_lines = []
            return
        meta, cleaned = _extract_event_meta(event_lines)
        event.body = cleaned
        if "id" in meta:
            event.id = meta["id"]
        if "start" in meta:
            event.start = meta["start"]
        if "end" in meta:
            event.end = meta["end"]
        if "session" in meta:
            event.session_id = meta["session"]
        if "status" in meta and meta["status"] in EVENT_STATUSES:
            event.status = meta["status"]
        if "trigger" in meta and meta["trigger"] in EVENT_TRIGGERS:
            event.trigger = meta["trigger"]
        if "rrule" in meta:
            event.rrule = meta["rrule"]
        if "all-day" in meta or "all_day" in meta:
            event.all_day = (meta.get("all-day") or meta.get("all_day") or "1") not in ("0", "false", "no", "")
        if "fire-from" in meta:
            event.fire_from = meta["fire-from"]
        if "fire-to" in meta:
            event.fire_to = meta["fire-to"]
        if "fire-every" in meta:
            try:
                event.fire_every_min = int(meta["fire-every"])
            except ValueError:
                pass
        if "model" in meta:
            event.model = meta["model"]
        if "assignee" in meta:
            event.assignee = meta["assignee"]
        elif "prompt" in meta or event.model:
            # Backward-compat: legacy events written before the assignee
            # gate existed implicitly meant "run with agent" when they had
            # a prompt or a model. Infer it so they keep firing; the next
            # write normalises the file. The legacy ``prompt`` value itself
            # is dropped — body now carries the agent context.
            event.assignee = "agent"
        if "completed" in meta:
            raw = meta["completed"] or ""
            event.completed_occurrences = [
                token for token in (t.strip() for t in raw.split(",")) if token
            ]
        cal.events.append(event)
        event = None
        event_lines = []

    for raw_line in body.split("\n"):
        line = raw_line
        if re.match(r"^# ", line):
            flush_event()
            cal.title = line[2:].strip()
            continue
        if line.startswith("### "):
            flush_event()
            event = Event(id="__pending__", title=line[4:].strip(), start="")
            continue
        if event is not None:
            event_lines.append(line)

    flush_event()
    return cal


def serialize(cal: Calendar) -> str:
    """Serialize a Calendar back into markdown."""
    fm = dict(cal.frontmatter)
    fm.setdefault(CALENDAR_PLUGIN_KEY, "basic")
    fm_text = yaml.dump(fm, default_flow_style=False, sort_keys=False).rstrip()
    out = [f"---\n{fm_text}\n---", "", f"# {cal.title}", ""]
    for event in cal.events:
        out.append(f"### {event.title}")
        out.append(f"<!-- nx:id={_ensure_id(event)} -->")
        if event.start:
            out.append(f"<!-- nx:start={event.start} -->")
        if event.end:
            out.append(f"<!-- nx:end={event.end} -->")
        out.append(f"<!-- nx:status={event.status} -->")
        if event.trigger is not None:
            out.append(f"<!-- nx:trigger={event.trigger} -->")
        if event.rrule:
            out.append(f"<!-- nx:rrule={event.rrule} -->")
        if event.session_id:
            out.append(f"<!-- nx:session={event.session_id} -->")
        if event.all_day:
            out.append("<!-- nx:all-day=1 -->")
        if event.fire_from:
            out.append(f"<!-- nx:fire-from={event.fire_from} -->")
        if event.fire_to:
            out.append(f"<!-- nx:fire-to={event.fire_to} -->")
        if event.fire_every_min:
            out.append(f"<!-- nx:fire-every={event.fire_every_min} -->")
        if event.model:
            out.append(f"<!-- nx:model={event.model} -->")
        if event.assignee:
            out.append(f"<!-- nx:assignee={event.assignee} -->")
        if event.completed_occurrences:
            out.append(
                f"<!-- nx:completed={','.join(event.completed_occurrences)} -->"
            )
        body = (event.body or "").strip()
        if body:
            out.append("")
            out.append(body)
        out.append("")
    return "\n".join(out).rstrip() + "\n"
