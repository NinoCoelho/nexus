/**
 * Tiny date helpers for the calendar view. Keeps the CalendarView free of
 * a heavy date library — these are all we need.
 *
 * - All API timestamps are UTC ISO-8601 strings ("2026-04-27T13:00:00Z").
 * - The grid renders in the user's local time zone via the native Date.
 * - For all-day events the API returns "YYYY-MM-DD" without a time component.
 */

export const DOW_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** Parse "...Z" or "YYYY-MM-DD". Returns Date in local time for "Z" inputs;
 *  for date-only inputs, returns local midnight on that date. */
export function parseEventStart(value: string): Date {
  if (!value) return new Date(NaN);
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const [y, m, d] = value.split("-").map(Number);
    return new Date(y, m - 1, d, 0, 0, 0, 0);
  }
  return new Date(value);
}

/** Format a Date as the UTC ISO string the API expects ("2026-04-27T13:00:00Z"). */
export function toIsoUtc(date: Date): string {
  return date.toISOString().replace(/\.\d{3}Z$/, "Z");
}

/** Format a Date as a local "YYYY-MM-DDTHH:MM" string for <input type="datetime-local">. */
export function toLocalInputValue(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  const h = String(date.getHours()).padStart(2, "0");
  const mn = String(date.getMinutes()).padStart(2, "0");
  return `${y}-${m}-${d}T${h}:${mn}`;
}

/** Parse a "YYYY-MM-DDTHH:MM" local input string into a UTC ISO Z string. */
export function fromLocalInputValue(value: string): string {
  const d = new Date(value);
  return toIsoUtc(d);
}

export function startOfDay(d: Date): Date {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  return x;
}

export function endOfDay(d: Date): Date {
  const x = new Date(d);
  x.setHours(23, 59, 59, 999);
  return x;
}

export function addDays(d: Date, n: number): Date {
  const x = new Date(d);
  x.setDate(x.getDate() + n);
  return x;
}

export function addMonths(d: Date, n: number): Date {
  const x = new Date(d);
  x.setMonth(x.getMonth() + n);
  return x;
}

export function startOfWeek(d: Date, weekStart = 0): Date {
  const x = startOfDay(d);
  const offset = (x.getDay() - weekStart + 7) % 7;
  x.setDate(x.getDate() - offset);
  return x;
}

export function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}

export function endOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth() + 1, 0, 23, 59, 59, 999);
}

export function isSameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

/** Window for the visible Month grid (always 6 rows × 7 cols). */
export function monthGridWindow(visible: Date, weekStart = 0): { from: Date; to: Date } {
  const first = startOfMonth(visible);
  const start = startOfWeek(first, weekStart);
  const end = addDays(start, 7 * 6);
  return { from: start, to: end };
}

export function weekWindow(visible: Date, weekStart = 0, days = 7): { from: Date; to: Date } {
  const start = days === 7 ? startOfWeek(visible, weekStart) : startOfDay(visible);
  return { from: start, to: addDays(start, days) };
}

export function formatHourLabel(h: number): string {
  if (h === 0) return "12 AM";
  if (h === 12) return "12 PM";
  return h < 12 ? `${h} AM` : `${h - 12} PM`;
}

export function formatMonthYear(d: Date): string {
  return d.toLocaleString(undefined, { month: "long", year: "numeric" });
}

export function formatWeekRange(d: Date, days = 7): string {
  const start = days === 7 ? startOfWeek(d) : startOfDay(d);
  const end = addDays(start, days - 1);
  if (start.getMonth() === end.getMonth()) {
    return `${start.toLocaleString(undefined, { month: "short" })} ${start.getDate()}–${end.getDate()}, ${start.getFullYear()}`;
  }
  return `${start.toLocaleString(undefined, { month: "short", day: "numeric" })} – ${end.toLocaleString(undefined, { month: "short", day: "numeric", year: "numeric" })}`;
}

export function formatDayRange(d: Date): string {
  return d.toLocaleString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" });
}

/** Minutes between two Dates. Defensive for negative durations. */
export function diffMinutes(a: Date, b: Date): number {
  return Math.max(0, Math.round((b.getTime() - a.getTime()) / 60000));
}
