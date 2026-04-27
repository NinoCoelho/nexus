/**
 * WeekGrid — header row with one column per day, an all-day strip below the
 * header, and a 24-hour body. Used for both Week (7 cols) and Day (1 col)
 * views.
 *
 * - All-day events render in a dedicated row above the hour grid, stacked
 *   vertically by lane (so multiple daily routines don't visually
 *   overlap).
 * - Timed events are absolutely positioned by start/end. Overlapping events
 *   in the same column share horizontal space side-by-side via a
 *   per-cluster column-assignment pass.
 * - A red current-time line is drawn on the today column.
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
const ALLDAY_ROW_PX = 22;
const ALLDAY_GAP_PX = 2;

interface Props {
  start: Date;
  days: number;
  events: CalendarEvent[];
  onSlotClick: (when: Date) => void;
  onEventClick: (ev: CalendarEvent) => void;
  onEventOpenChat: (ev: CalendarEvent) => void;
  onEventFire: (ev: CalendarEvent) => void;
}

function isAgentAssigned(ev: CalendarEvent): boolean {
  return ev.assignee === "agent";
}

interface PositionedEvent {
  ev: CalendarEvent;
  startMin: number;
  endMin: number;
  col: number;     // column index inside its overlap cluster
  cols: number;    // total columns in this cluster
}

/**
 * Sweep-line column assignment for overlapping events. Events with strictly
 * positive overlap (>= 1 minute) share a cluster and split horizontal space.
 */
function layoutTimedEvents(events: { ev: CalendarEvent; startMin: number; endMin: number }[]): PositionedEvent[] {
  const sorted = [...events].sort((a, b) => a.startMin - b.startMin || a.endMin - b.endMin);
  const out: PositionedEvent[] = [];

  let cluster: { item: typeof sorted[number]; col: number }[] = [];
  let clusterEnd = -1;

  function flush() {
    if (cluster.length === 0) return;
    const cols = Math.max(...cluster.map((c) => c.col)) + 1;
    for (const c of cluster) {
      out.push({ ...c.item, col: c.col, cols });
    }
    cluster = [];
    clusterEnd = -1;
  }

  for (const item of sorted) {
    if (cluster.length === 0 || item.startMin >= clusterEnd) {
      // No overlap with the current cluster — emit and reset.
      flush();
      cluster.push({ item, col: 0 });
      clusterEnd = item.endMin;
      continue;
    }
    // Find the lowest free column.
    const used = new Set(
      cluster
        .filter((c) => c.item.endMin > item.startMin)
        .map((c) => c.col),
    );
    let col = 0;
    while (used.has(col)) col++;
    cluster.push({ item, col });
    if (item.endMin > clusterEnd) clusterEnd = item.endMin;
  }
  flush();
  return out;
}

/** Lane assignment for stacked all-day events on a single day. Always lane 0
 *  for the first event of a day, lane 1 for the second, etc. */
function laneFor(events: CalendarEvent[]): Map<string, number> {
  const lanes = new Map<string, number>();
  events.forEach((ev, i) => {
    lanes.set(`${ev.id}::${ev.occurrence_start ?? ""}`, i);
  });
  return lanes;
}

export default function WeekGrid({
  start, days, events, onSlotClick, onEventClick, onEventOpenChat, onEventFire,
}: Props) {
  const today = new Date();
  const cols: Date[] = [];
  for (let i = 0; i < days; i++) cols.push(addDays(start, i));

  function eventsForCol(col: Date): CalendarEvent[] {
    return events.filter((ev) => {
      const s = parseEventStart(ev.occurrence_start ?? ev.start);
      return isSameDay(s, col);
    });
  }

  // Per-day layout: all-day events get their own lane row; timed events get
  // overlap-aware column assignments.
  const perCol = cols.map((c) => {
    const dayEvents = eventsForCol(c);
    const allDay = dayEvents.filter((e) => e.all_day);
    const timed = dayEvents.filter((e) => !e.all_day);
    const positioned = layoutTimedEvents(
      timed.map((ev) => {
        const s = parseEventStart(ev.occurrence_start ?? ev.start);
        const startMin = s.getHours() * 60 + s.getMinutes();
        let endMin = startMin + 30;
        if (ev.end) {
          const end = parseEventStart(ev.end);
          endMin = Math.max(startMin + 15, end.getHours() * 60 + end.getMinutes());
        }
        return { ev, startMin, endMin };
      }),
    );
    return { date: c, allDay, lanes: laneFor(allDay), positioned };
  });

  const allDayLaneCount = Math.max(0, ...perCol.map((p) => p.allDay.length));
  const allDayHeight =
    allDayLaneCount > 0
      ? allDayLaneCount * ALLDAY_ROW_PX + (allDayLaneCount - 1) * ALLDAY_GAP_PX + 8
      : 0;

  function timedStyle(p: PositionedEvent): CSSProperties {
    const top = (p.startMin / 60) * HOUR_PX;
    const height = Math.max(20, ((p.endMin - p.startMin) / 60) * HOUR_PX);
    // Column-split: 4px gutter between events; events touch the col edges.
    const widthPct = 100 / p.cols;
    const leftPct = p.col * widthPct;
    const gutter = p.cols > 1 ? 2 : 0;
    return {
      top,
      height,
      left: `calc(${leftPct}% + ${p.col === 0 ? 4 : gutter}px)`,
      width: `calc(${widthPct}% - ${p.col === 0 || p.col === p.cols - 1 ? gutter + 4 : gutter * 2}px)`,
      // When events overlap, give the rightmost slight z-index priority so
      // its border is visible.
      zIndex: 1 + p.col,
    };
  }

  // Current-time line position (only meaningful if today is in view).
  const todayInView = cols.some((c) => isSameDay(c, today));
  const nowMin = today.getHours() * 60 + today.getMinutes();
  const nowTop = (nowMin / 60) * HOUR_PX;

  return (
    <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
      {/* Header row */}
      <div
        style={{
          display: "flex",
          position: "sticky",
          top: 0,
          zIndex: 3,
          background: "var(--bg)",
        }}
      >
        <div
          style={{
            width: 56,
            flexShrink: 0,
            borderBottom: "1px solid var(--border)",
            borderRight: "1px solid var(--border)",
          }}
        />
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

      {/* All-day row (only rendered when at least one all-day event is in view) */}
      {allDayLaneCount > 0 && (
        <div
          className="cal-week-allday-row"
          style={{ display: "flex", height: allDayHeight }}
        >
          <div className="cal-week-allday-gutter">all-day</div>
          {perCol.map((p, colIdx) => (
            <div
              key={`ad-${colIdx}`}
              className={`cal-week-allday-col${isSameDay(p.date, today) ? " today" : ""}`}
              style={{ flex: 1 }}
            >
              {p.allDay.map((ev) => {
                const lane = p.lanes.get(`${ev.id}::${ev.occurrence_start ?? ""}`) ?? 0;
                const top = lane * (ALLDAY_ROW_PX + ALLDAY_GAP_PX) + 4;
                return (
                  <div
                    key={`ad-${ev.id}-${ev.occurrence_start ?? ""}`}
                    className={`cal-week-allday-event cal-event--${ev.status}`}
                    style={{ top, height: ALLDAY_ROW_PX }}
                    onClick={(e) => { e.stopPropagation(); onEventClick(ev); }}
                    title={`${ev.title} — ${ev.status}`}
                  >
                    {isAgentAssigned(ev) && (
                      <span style={{ marginRight: 3 }}>⚡</span>
                    )}
                    {ev.title}
                    {ev.status === "missed" && (
                      <button
                        className="cal-event-fire-btn"
                        onClick={(e) => { e.stopPropagation(); onEventFire(ev); }}
                        title="Fire now"
                      >▶</button>
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      )}

      {/* Body: hour-label column + N day columns */}
      <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
        <div style={{ width: 56, flexShrink: 0 }}>
          {Array.from({ length: 24 }).map((_, hour) => (
            <div key={`hl-${hour}`} className="cal-week-hour-label">
              {formatHourLabel(hour)}
            </div>
          ))}
        </div>
        {perCol.map((p, colIdx) => (
          <div
            key={`col-${colIdx}`}
            className={`cal-week-col${isSameDay(p.date, today) ? " today" : ""}`}
            style={{ flex: 1, position: "relative", minWidth: 0 }}
          >
            {/* hour cells */}
            {Array.from({ length: 24 }).map((_, hour) => {
              const slotDate = new Date(p.date);
              slotDate.setHours(hour, 0, 0, 0);
              return (
                <div
                  key={`cell-${colIdx}-${hour}`}
                  className="cal-week-cell"
                  onClick={() => onSlotClick(slotDate)}
                />
              );
            })}
            {/* current-time line on today's column */}
            {todayInView && isSameDay(p.date, today) && (
              <div className="cal-now-line" style={{ top: nowTop }} />
            )}
            {/* timed events overlay */}
            {p.positioned.map((pos) => {
              const ev = pos.ev;
              return (
                <div
                  key={`ev-${ev.id}-${ev.occurrence_start ?? ""}`}
                  className={`cal-week-event cal-event--${ev.status}`}
                  style={timedStyle(pos)}
                  onClick={(e) => { e.stopPropagation(); onEventClick(ev); }}
                  title={`${ev.title} — ${ev.status}`}
                >
                  <div
                    style={{
                      fontWeight: 500,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {isAgentAssigned(ev) && (
                      <span style={{ marginRight: 3 }}>⚡</span>
                    )}
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
        ))}
      </div>
    </div>
  );
}
