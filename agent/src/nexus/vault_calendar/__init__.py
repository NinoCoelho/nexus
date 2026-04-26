"""Vault-native calendar — single .md file with `calendar-plugin: basic` frontmatter.

A calendar is a markdown file in the vault. Frontmatter declares the plugin and
calendar-level metadata (timezone, prompt, default trigger). Each event is a
``### Title`` heading followed by ``<!-- nx:* -->`` metadata comments.

    ---
    calendar-plugin: basic
    calendar_prompt: |
      You are the assistant for this calendar...
    timezone: America/Sao_Paulo
    auto_trigger: true
    ---

    # Calendar title

    ### Standup
    <!-- nx:id=<uuid> -->
    <!-- nx:start=2026-04-27T13:00:00Z -->
    <!-- nx:end=2026-04-27T13:30:00Z -->
    <!-- nx:status=scheduled -->
    <!-- nx:trigger=on_start -->
    <!-- nx:rrule=FREQ=WEEKLY;BYDAY=MO -->   (optional)
    <!-- nx:session=<sid> -->                 (set when fired)
    <!-- nx:all-day=1 -->                     (optional)

    free-form body / notes

The plain-markdown file remains hand-editable; ``<!-- nx:* -->`` comments are
invisible in any viewer. Storage is UTC ISO-8601 throughout — ``timezone`` in
frontmatter is purely a UI hint for rendering and creation.
"""

from .bootstrap import ensure_default_calendar, sweep_missed
from .calendars import (
    CalendarSummary,
    create_empty,
    list_calendars,
    query_events,
    read_calendar,
    update_calendar,
    write_calendar,
)
from .events import (
    add_event,
    delete_event,
    effective_model,
    effective_prompt,
    effective_trigger,
    find_event,
    fire_event,
    move_event,
    update_event,
)
from .models import (
    CALENDAR_PLUGIN_KEY,
    EVENT_STATUSES,
    EVENT_TRIGGERS,
    Calendar,
    Event,
    is_calendar_file,
)
from .parser import parse, serialize
from .recurrence import expand_window, next_occurrence_after

__all__ = [
    # models
    "Calendar",
    "Event",
    "CALENDAR_PLUGIN_KEY",
    "EVENT_STATUSES",
    "EVENT_TRIGGERS",
    "is_calendar_file",
    # parser
    "parse",
    "serialize",
    # calendars
    "CalendarSummary",
    "read_calendar",
    "write_calendar",
    "create_empty",
    "list_calendars",
    "update_calendar",
    "query_events",
    # events
    "add_event",
    "update_event",
    "move_event",
    "delete_event",
    "find_event",
    "fire_event",
    "effective_trigger",
    "effective_prompt",
    "effective_model",
    # recurrence
    "expand_window",
    "next_occurrence_after",
    # bootstrap
    "ensure_default_calendar",
    "sweep_missed",
]
