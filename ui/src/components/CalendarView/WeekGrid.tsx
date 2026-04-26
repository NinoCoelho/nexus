/**
 * WeekGrid — header row with one column per day + a 24-hour body. Used for
 * both Week (7 cols) and Day (1 col) views. Events are placed as absolutely
 * positioned blocks calculated from start/end. Click an empty hour to
 * create at that time; click a block to edit.
 */

import type { CSSProperties } from "react";
import type { CalendarEvent } from "../../api/calendar";
import {
  addDays,
  formatHourLabel,
  isSameDay,
  parseEventStart,
} from "./dateUtils";

const HOUR_PX = 40;

interface Props {
  start: Date;
  days: number;
  events: CalendarEvent[];
  calendarHasPrompt: boolean;
  onSlotClick: (when: Date) => void;
  onEventClick: (ev: CalendarEvent) => void;
  onEventOpenChat: (ev: CalendarEvent) => void;
  onEventFire: (ev: CalendarEvent) => void;
}

function hasEffectivePrompt(ev: CalendarEvent, calendarHasPrompt: boolean): boolean {
  if (ev.prompt && ev.prompt.trim()) return true;
  return calendarHasPrompt;
}

export default function WeekGrid({
  start, days, events, calendarHasPrompt, onSlotClick, onEventClick, onEventOpenChat, onEventFire,
}: Props) {
  const today = new Date();
  const cols: Date[] = [];
  for (let i = 0; i < days; i++) cols.push(addDays(start, i));

  function eventsForCol(col: Date): CalendarEvent[] {
    return events.filter((ev) => {
      const start = parseEventStart(ev.occurrence_start ?? ev.start);
      return isSameDay(start, col);
    });
  }

  function eventStyle(ev: CalendarEvent): CSSProperties {
    const startD = parseEventStart(ev.occurrence_start ?? ev.start);
    const startMin = startD.getHours() * 60 + startD.getMinutes();
    let endMin = startMin + 30;
    if (ev.end) {
      const end = parseEventStart(ev.end);
      endMin = Math.max(startMin + 15, end.getHours() * 60 + end.getMinutes());
    }
    const top = (startMin / 60) * HOUR_PX;
    const height = Math.max(20, ((endMin - startMin) / 60) * HOUR_PX);
    return { top, height };
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
      {/* Header row */}
      <div style={{ display: "flex", position: "sticky", top: 0, zIndex: 2, background: "var(--bg)" }}>
        <div style={{ width: 56, flexShrink: 0, borderBottom: "1px solid var(--border)", borderRight: "1px solid var(--border)" }} />
        {cols.map((c, i) => (
          <div
            key={`hdr-${i}`}
            className={`cal-week-header-cell${isSameDay(c, today) ? " today" : ""}`}
            style={{ flex: 1 }}
          >
            <div>{c.toLocaleString(undefined, { weekday: "short" })}</div>
            <div style={{ fontSize: 16, fontWeight: 600 }}>{c.getDate()}</div>
          </div>
        ))}
      </div>

      {/* Body: hour-label column + N day columns */}
      <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
        <div style={{ width: 56, flexShrink: 0 }}>
          {Array.from({ length: 24 }).map((_, hour) => (
            <div key={`hl-${hour}`} className="cal-week-hour-label">
              {formatHourLabel(hour)}
            </div>
          ))}
        </div>
        {cols.map((c, colIdx) => {
          const dayEvents = eventsForCol(c);
          return (
            <div
              key={`col-${colIdx}`}
              style={{ flex: 1, position: "relative", minWidth: 0 }}
            >
              {/* hour cells */}
              {Array.from({ length: 24 }).map((_, hour) => {
                const slotDate = new Date(c);
                slotDate.setHours(hour, 0, 0, 0);
                return (
                  <div
                    key={`cell-${colIdx}-${hour}`}
                    className="cal-week-cell"
                    onClick={() => onSlotClick(slotDate)}
                  />
                );
              })}
              {/* events overlay */}
              {dayEvents.map((ev) => {
                if (ev.all_day) {
                  return (
                    <div
                      key={`ev-${ev.id}-${ev.occurrence_start ?? ""}`}
                      className={`cal-week-event cal-event--${ev.status}`}
                      style={{ top: 0, height: 22 }}
                      onClick={(e) => { e.stopPropagation(); onEventClick(ev); }}
                    >
                      {ev.title}
                    </div>
                  );
                }
                return (
                  <div
                    key={`ev-${ev.id}-${ev.occurrence_start ?? ""}`}
                    className={`cal-week-event cal-event--${ev.status}`}
                    style={eventStyle(ev)}
                    onClick={(e) => { e.stopPropagation(); onEventClick(ev); }}
                    title={`${ev.title} — ${ev.status}`}
                  >
                    <div style={{ fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {hasEffectivePrompt(ev, calendarHasPrompt) && <span style={{ marginRight: 3 }}>⚡</span>}
                      {ev.title}
                    </div>
                    <div style={{ fontSize: 10, opacity: 0.85 }}>
                      {parseEventStart(ev.start).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}
                    </div>
                    <div style={{ display: "flex", gap: 4, marginTop: 2 }}>
                      {ev.status === "missed" && (
                        <button
                          className="cal-event-fire-btn"
                          onClick={(e) => { e.stopPropagation(); onEventFire(ev); }}
                          title="Fire now"
                        >▶</button>
                      )}
                      <button
                        className="cal-event-chat-btn"
                        onClick={(e) => { e.stopPropagation(); onEventOpenChat(ev); }}
                        title="Open in chat"
                      >💬</button>
                    </div>
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
    </div>
  );
}
