/**
 * MonthGrid — 6 rows × 7 cols. Each cell shows the day number plus up to 3
 * event chips. Click an empty cell to create at noon; click an event chip to
 * edit.
 */

import type { CalendarEvent } from "../../api/calendar";
import {
  DOW_LABELS,
  addDays,
  isSameDay,
  monthGridWindow,
  parseEventStart,
  startOfMonth,
} from "./dateUtils";

interface Props {
  visible: Date;
  events: CalendarEvent[];
  onCellClick: (day: Date) => void;
  onEventClick: (ev: CalendarEvent) => void;
  onEventOpenChat: (ev: CalendarEvent) => void;
  onEventFire: (ev: CalendarEvent) => void;
}

function isAgentAssigned(ev: CalendarEvent): boolean {
  return ev.assignee === "agent";
}

export default function MonthGrid({ visible, events, onCellClick, onEventClick, onEventOpenChat, onEventFire }: Props) {
  const { from } = monthGridWindow(visible);
  const today = new Date();
  const monthIdx = startOfMonth(visible).getMonth();

  const days: Date[] = [];
  for (let i = 0; i < 42; i++) days.push(addDays(from, i));

  function eventsForDay(day: Date): CalendarEvent[] {
    return events
      .filter((ev) => {
        const start = parseEventStart(ev.occurrence_start ?? ev.start);
        return isSameDay(start, day);
      })
      .sort((a, b) => parseEventStart(a.occurrence_start ?? a.start).getTime() - parseEventStart(b.occurrence_start ?? b.start).getTime());
  }

  return (
    <>
      <div className="cal-month-dow">
        {DOW_LABELS.map((d) => (
          <div key={d}>{d}</div>
        ))}
      </div>
      <div className="cal-month">
        {days.map((day, i) => {
          const inMonth = day.getMonth() === monthIdx;
          const today_ = isSameDay(day, today);
          const dayEvents = eventsForDay(day);
          return (
            <div
              key={i}
              className={`cal-month-cell${inMonth ? "" : " other-month"}${today_ ? " today" : ""}`}
              onClick={() => onCellClick(day)}
            >
              <span className="cal-day-num">{day.getDate()}</span>
              {dayEvents.slice(0, 3).map((ev) => (
                <div
                  key={ev.id + (ev.occurrence_start ?? "")}
                  className={`cal-event cal-event--${ev.status}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    onEventClick(ev);
                  }}
                  title={`${ev.title} — ${ev.status}${isAgentAssigned(ev) ? " (auto-runs agent)" : ""}`}
                >
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
                    {isAgentAssigned(ev) && <span style={{ marginRight: 3 }}>⚡</span>}
                    {!ev.all_day && parseEventStart(ev.start).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}{" "}
                    {ev.title}
                  </span>
                  {ev.status === "missed" && (
                    <button
                      className="cal-event-fire-btn"
                      title="Fire now"
                      onClick={(e) => { e.stopPropagation(); onEventFire(ev); }}
                    >▶</button>
                  )}
                  <button
                    className="cal-event-chat-btn"
                    title="Open in chat"
                    onClick={(e) => { e.stopPropagation(); onEventOpenChat(ev); }}
                  >💬</button>
                </div>
              ))}
              {dayEvents.length > 3 && (
                <div style={{ fontSize: 11, color: "var(--fg-faint)" }}>
                  +{dayEvents.length - 3} more
                </div>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}
