import type { ActiveAlarm } from "../hooks/useCalendarAlarms";
import "./AlarmNotification.css";

interface Props {
  alarms: ActiveAlarm[];
  onDismiss: (eventId: string, occurrenceStart: string) => void;
  onSnooze: (eventId: string, occurrenceStart: string, minutes: number) => void;
  onOpen: (path: string) => void;
}

function formatCountdown(seconds: number): string {
  if (seconds <= 0) return "DUE NOW";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  if (m < 60) return `${m}m ${s}s`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return `${h}h ${rm}m`;
}

function formatStartTime(start: string): string {
  if (!start) return "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(start)) {
    const dt = new Date(start + "T00:00:00Z");
    return dt.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
  const dt = new Date(start);
  if (Number.isNaN(dt.getTime())) return start;
  return dt.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export default function AlarmNotification({ alarms, onDismiss, onSnooze, onOpen }: Props) {
  if (alarms.length === 0) return null;

  return (
    <div className="alarm-stack">
      {alarms.map((alarm) => (
        <div
          key={`${alarm.eventId}-${alarm.occurrenceStart}`}
          className={`alarm-card${alarm.isOverdue ? " alarm-card--overdue" : ""}`}
        >
          <div className="alarm-header">
            <svg className="alarm-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
              <path d="M13.73 21a2 2 0 0 1-3.46 0" />
            </svg>
            <span className="alarm-title">{alarm.title}</span>
          </div>

          <div className="alarm-countdown">
            {formatCountdown(alarm.countdownSeconds)}
          </div>

          <div className="alarm-meta">
            <span>{formatStartTime(alarm.start)}</span>
            {alarm.calendarTitle && <span>{alarm.calendarTitle}</span>}
          </div>

          <div className="alarm-actions">
            <button onClick={() => onSnooze(alarm.eventId, alarm.occurrenceStart, 5)}>
              Snooze 5m
            </button>
            <button onClick={() => onSnooze(alarm.eventId, alarm.occurrenceStart, 15)}>
              15m
            </button>
            <button onClick={() => onOpen(alarm.path)}>Open</button>
            <button
              className="alarm-btn--dismiss"
              onClick={() => onDismiss(alarm.eventId, alarm.occurrenceStart)}
            >
              Dismiss
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
