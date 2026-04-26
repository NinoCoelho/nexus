/**
 * @file API client for vault-native Calendars.
 *
 * Each calendar is a Markdown file in the vault with `calendar-plugin: basic`
 * frontmatter. CRUD goes through `/vault/calendar/*` endpoints.
 */
import { BASE } from "./base";

export type EventStatus =
  | "scheduled"
  | "triggered"
  | "done"
  | "failed"
  | "missed"
  | "cancelled";

export type EventTrigger = "on_start" | "off";

export interface CalendarEvent {
  id: string;
  title: string;
  start: string;
  end?: string;
  body?: string;
  status: EventStatus;
  trigger?: EventTrigger | null;
  rrule?: string | null;
  session_id?: string | null;
  all_day?: boolean;
  prompt?: string | null;
  fire_from?: string | null;
  fire_to?: string | null;
  fire_every_min?: number | null;
  /** Per-event model id used when the agent runs this event. */
  model?: string | null;
  /** ``"agent"`` opts the event into auto-firing; anything else is a plain entry. */
  assignee?: string | null;
  /** Present in range queries — UTC ISO of the resolved (recurring) occurrence. */
  occurrence_start?: string;
  /** Present in range queries — vault-relative file path. */
  path?: string;
  calendar_title?: string;
}

export interface Calendar {
  path: string;
  title: string;
  events: CalendarEvent[];
  calendar_prompt?: string | null;
  timezone?: string;
  auto_trigger?: boolean;
  default_duration_min?: number;
}

export interface CalendarSummary {
  path: string;
  title: string;
  timezone: string;
  auto_trigger: boolean;
  event_count: number;
}

export interface CalendarListResult {
  calendars: CalendarSummary[];
  count: number;
}

export interface EventQuery {
  from?: string;
  to?: string;
  status?: EventStatus;
  calendar_path?: string;
  limit?: number;
}

async function jsonOrThrow<T>(res: Response, label: string): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `${label}: ${res.status}`);
  }
  return res.json();
}

export async function getVaultCalendar(path: string): Promise<Calendar> {
  const res = await fetch(`${BASE}/vault/calendar?path=${encodeURIComponent(path)}`);
  return jsonOrThrow<Calendar>(res, "Calendar load error");
}

export async function listVaultCalendars(): Promise<CalendarListResult> {
  const res = await fetch(`${BASE}/vault/calendar/list`);
  return jsonOrThrow<CalendarListResult>(res, "Calendar list error");
}

export async function createVaultCalendar(
  path: string,
  opts: { title?: string; timezone?: string; prompt?: string } = {},
): Promise<Calendar> {
  const res = await fetch(`${BASE}/vault/calendar`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, ...opts }),
  });
  return jsonOrThrow<Calendar>(res, "Calendar create error");
}

export async function patchVaultCalendar(
  path: string,
  updates: {
    title?: string;
    prompt?: string;
    timezone?: string;
    auto_trigger?: boolean;
    default_duration_min?: number;
  },
): Promise<Calendar> {
  const res = await fetch(
    `${BASE}/vault/calendar?path=${encodeURIComponent(path)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    },
  );
  return jsonOrThrow<Calendar>(res, "Calendar update error");
}

export async function addVaultCalendarEvent(
  path: string,
  body: {
    title: string;
    start: string;
    end?: string;
    body?: string;
    trigger?: EventTrigger;
    rrule?: string;
    all_day?: boolean;
    status?: EventStatus;
    prompt?: string;
    fire_from?: string;
    fire_to?: string;
    fire_every_min?: number;
    model?: string;
    assignee?: string;
  },
): Promise<CalendarEvent> {
  const res = await fetch(
    `${BASE}/vault/calendar/events?path=${encodeURIComponent(path)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  return jsonOrThrow<CalendarEvent>(res, "Event create error");
}

export async function patchVaultCalendarEvent(
  path: string,
  eventId: string,
  updates: Partial<{
    title: string;
    body: string;
    start: string;
    end: string | null;
    status: EventStatus;
    trigger: EventTrigger | "";
    rrule: string | null;
    all_day: boolean;
    session_id: string | null;
    prompt: string | null;
    fire_from: string | null;
    fire_to: string | null;
    fire_every_min: number | null;
    model: string | null;
    assignee: string | null;
  }>,
): Promise<CalendarEvent> {
  const res = await fetch(
    `${BASE}/vault/calendar/events/${encodeURIComponent(eventId)}?path=${encodeURIComponent(path)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    },
  );
  return jsonOrThrow<CalendarEvent>(res, "Event update error");
}

export async function deleteVaultCalendarEvent(
  path: string,
  eventId: string,
): Promise<void> {
  const res = await fetch(
    `${BASE}/vault/calendar/events/${encodeURIComponent(eventId)}?path=${encodeURIComponent(path)}`,
    { method: "DELETE" },
  );
  if (!res.ok && res.status !== 204) {
    throw new Error(`Event delete error: ${res.status}`);
  }
}

export async function fireVaultCalendarEvent(
  path: string,
  eventId: string,
): Promise<{ session_id: string }> {
  const res = await fetch(
    `${BASE}/vault/calendar/events/${encodeURIComponent(eventId)}/fire?path=${encodeURIComponent(path)}`,
    { method: "POST" },
  );
  return jsonOrThrow<{ session_id: string }>(res, "Event fire error");
}

export async function queryVaultCalendarEvents(
  q: EventQuery,
): Promise<{ events: CalendarEvent[]; count: number }> {
  const res = await fetch(`${BASE}/vault/calendar/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(q),
  });
  return jsonOrThrow<{ events: CalendarEvent[]; count: number }>(res, "Event query error");
}
