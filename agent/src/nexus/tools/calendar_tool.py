"""Calendar agent tool: calendar_manage.

Operates on calendars stored as markdown files in the vault. The agent
addresses calendars by their vault-relative path (e.g.
"Calendars/Default.md"). If the file doesn't exist yet, use
action="create_calendar" to scaffold one.
"""

from __future__ import annotations

import json
from typing import Any

from ..agent.llm import ToolSpec

CALENDAR_MANAGE_TOOL = ToolSpec(
    name="calendar_manage",
    description=(
        "Manage calendars stored as markdown in the vault. Each calendar is a "
        "single .md file with `calendar-plugin: basic` frontmatter. Events "
        "have UTC ISO-8601 start/end and may have a `trigger=on_start` flag "
        "that auto-fires the agent when the time arrives. Recurring events "
        "use iCal RRULE (e.g. `FREQ=DAILY`, `FREQ=WEEKLY;BYDAY=MO,WE,FR`). "
        "All-day events use `all_day=true` and a `YYYY-MM-DD` start; set "
        "`trigger=off` for events that are records only and shouldn't fire "
        "the agent. Actions: list_calendars, create_calendar, view, "
        "update_calendar, list_events, add_event, update_event, move_event, "
        "delete_event."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list_calendars",
                    "create_calendar", "view", "update_calendar", "list_events",
                    "add_event", "update_event", "move_event", "delete_event",
                ],
                "description": "Action to perform.",
            },
            "path": {
                "type": "string",
                "description": "Vault-relative path to the calendar .md file. Not required for list_calendars.",
            },
            "title": {
                "type": "string",
                "description": "Calendar title (create/update_calendar) or event title (add_event/update_event).",
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Calendar-level prompt injected when an event auto-fires. "
                    "Empty string clears."
                ),
            },
            "timezone": {
                "type": "string",
                "description": "IANA timezone for the calendar (e.g. 'America/Sao_Paulo').",
            },
            "auto_trigger": {
                "type": "boolean",
                "description": "Calendar-level default for whether new events auto-fire (update_calendar).",
            },
            "event_id": {"type": "string", "description": "Event id (update/move/delete)."},
            "start": {
                "type": "string",
                "description": "ISO-8601 UTC ('2026-04-27T13:00:00Z') or 'YYYY-MM-DD' for all-day.",
            },
            "end": {"type": "string", "description": "Event end (same format as start). Optional."},
            "body": {"type": "string", "description": "Event body / agenda markdown."},
            "trigger": {
                "type": "string",
                "enum": ["", "on_start", "off"],
                "description": (
                    "Per-event override for auto-firing. Empty string clears (inherits "
                    "calendar-level auto_trigger)."
                ),
            },
            "rrule": {
                "type": "string",
                "description": "iCal RRULE (e.g. 'FREQ=WEEKLY;BYDAY=MO'). Empty string clears.",
            },
            "all_day": {"type": "boolean", "description": "Mark event as all-day."},
            "event_prompt": {
                "type": "string",
                "description": (
                    "Per-event prompt. When set (or inherited from the calendar's "
                    "prompt), the agent runs at the event's fire time with this "
                    "prompt as system context. When empty AND the calendar has no "
                    "prompt, the event only triggers a notification (calendar_alert) "
                    "to the user — like a plain reminder. Empty string clears."
                ),
            },
            "fire_from": {
                "type": "string",
                "description": (
                    "All-day events: local 'HH:MM' window start. Combined with "
                    "fire_to and fire_every_min, the agent is auto-fired every "
                    "N minutes during this window each active day. Empty string clears."
                ),
            },
            "fire_to": {
                "type": "string",
                "description": "All-day events: local 'HH:MM' window end. Empty string clears.",
            },
            "fire_every_min": {
                "type": "integer",
                "description": (
                    "All-day events: minutes between auto-fires inside the window "
                    "(e.g. 30 = every half hour). Set 0 to clear."
                ),
            },
            "model": {
                "type": "string",
                "description": (
                    "Per-event model id used when the agent runs this event. "
                    "Falls back to the calendar's default_model, then the agent's "
                    "configured default. Empty string clears."
                ),
            },
            "default_model": {
                "type": "string",
                "description": (
                    "Calendar-level default model id (update_calendar). Empty string clears."
                ),
            },
            "assignee": {
                "type": "string",
                "description": (
                    "Set to 'agent' to opt this event into auto-firing (heartbeat "
                    "dispatch). Any other value (or empty string) makes the event "
                    "a plain calendar entry that never fires."
                ),
            },
            "status": {
                "type": "string",
                "enum": ["", "scheduled", "triggered", "done", "failed", "missed", "cancelled"],
                "description": "Event status. Empty string keeps current.",
            },
            "from": {"type": "string", "description": "Range query start (ISO-8601 UTC) for list_events."},
            "to": {"type": "string", "description": "Range query end (ISO-8601 UTC) for list_events."},
        },
        "required": ["action"],
    },
)


def handle_calendar_tool(args: dict[str, Any]) -> str:
    """Dispatch the requested calendar action and return serialized JSON."""
    from .. import vault_calendar

    action = args.get("action", "")
    path = args.get("path", "")

    try:
        if action == "list_calendars":
            items = [s.to_dict() for s in vault_calendar.list_calendars()]
            return json.dumps({"ok": True, "calendars": items, "count": len(items)})

        if not path:
            return json.dumps({"ok": False, "error": "`path` is required"})

        if action == "create_calendar":
            cal = vault_calendar.create_empty(
                path,
                title=args.get("title"),
                timezone=args.get("timezone"),
                prompt=args.get("prompt"),
            )
            return json.dumps({"ok": True, "path": path, "calendar": cal.to_dict()})

        if action == "view":
            cal = vault_calendar.read_calendar(path)
            return json.dumps({"ok": True, "path": path, "calendar": cal.to_dict()})

        if action == "update_calendar":
            updates: dict[str, Any] = {}
            for key in ("title", "prompt", "timezone", "auto_trigger", "default_duration_min", "default_model"):
                if key in args:
                    updates[key] = args[key]
            if not updates:
                return json.dumps({"ok": False, "error": "no fields to update"})
            cal = vault_calendar.update_calendar(path, updates)
            return json.dumps({"ok": True, "path": path, "calendar": cal.to_dict()})

        if action == "list_events":
            hits = vault_calendar.query_events(
                from_utc=args.get("from"),
                to_utc=args.get("to"),
                status=args.get("status") or None,
                calendar_path=path,
            )
            return json.dumps({"ok": True, "events": hits, "count": len(hits)})

        if action == "add_event":
            title = args.get("title", "")
            start = args.get("start", "")
            if not title or not start:
                return json.dumps({"ok": False, "error": "`title` and `start` are required"})
            fire_every = args.get("fire_every_min")
            ev = vault_calendar.add_event(
                path,
                title=title,
                start=start,
                end=args.get("end"),
                body=args.get("body", ""),
                trigger=args.get("trigger") or None,
                rrule=args.get("rrule") or None,
                all_day=bool(args.get("all_day", False)),
                status=args.get("status") or "scheduled",
                prompt=args.get("event_prompt") or None,
                fire_from=args.get("fire_from") or None,
                fire_to=args.get("fire_to") or None,
                fire_every_min=int(fire_every) if fire_every else None,
                model=args.get("model") or None,
                assignee=args.get("assignee") or None,
            )
            return json.dumps({"ok": True, "event": ev.to_dict()})

        if action == "update_event":
            event_id = args.get("event_id", "")
            if not event_id:
                return json.dumps({"ok": False, "error": "`event_id` is required"})
            updates = {}
            for key in (
                "title", "body", "start", "end", "status", "trigger", "rrule",
                "all_day", "session_id", "fire_from", "fire_to", "fire_every_min",
                "model", "assignee",
            ):
                if key in args:
                    updates[key] = args[key]
            if "event_prompt" in args:
                updates["prompt"] = args["event_prompt"]
            ev = vault_calendar.update_event(path, event_id, updates)
            return json.dumps({"ok": True, "event": ev.to_dict()})

        if action == "move_event":
            event_id = args.get("event_id", "")
            new_start = args.get("start", "")
            if not event_id or not new_start:
                return json.dumps({"ok": False, "error": "`event_id` and `start` are required"})
            ev = vault_calendar.move_event(path, event_id, new_start, args.get("end"))
            return json.dumps({"ok": True, "event": ev.to_dict()})

        if action == "delete_event":
            event_id = args.get("event_id", "")
            if not event_id:
                return json.dumps({"ok": False, "error": "`event_id` is required"})
            vault_calendar.delete_event(path, event_id)
            return json.dumps({"ok": True})

        return json.dumps({"ok": False, "error": f"unknown action: {action!r}"})

    except (KeyError, ValueError, FileNotFoundError, OSError) as exc:
        return json.dumps({"ok": False, "error": str(exc)})
