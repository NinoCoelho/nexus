/**
 * EventDetailModal — read-only view for a calendar event.
 *
 * Clicking an event opens this modal first. The body/notes are rendered as
 * markdown via MarkdownView so vault links, checklists, tables, etc. all
 * render properly. An "Edit" button transitions to the full EventModal form.
 *
 * When the event is agent-assigned and fired, a spinner (same style as kanban)
 * appears and the modal polls the server until the agent finishes. The status
 * button uses a two-click confirm pattern for stop/retry, matching kanban UX.
 */

import { useEffect, useRef, useState } from "react";
import type { CalendarEvent } from "../../api/calendar";
import {
  fireVaultCalendarEvent,
  getVaultCalendar,
  patchVaultCalendarEvent,
} from "../../api/calendar";
import MarkdownView from "../MarkdownView";
import { parseEventStart } from "./dateUtils";

interface Props {
  event: CalendarEvent;
  calendarPath: string;
  onEdit: (updatedEvent: CalendarEvent) => void;
  onDelete: () => void;
  onClose: () => void;
  onReload: () => void;
  onOpenInChat?: (ev: CalendarEvent) => void;
}

type ConfirmVariant = "idle" | "confirm-stop" | "confirm-retry";

function formatDate(ev: CalendarEvent): string {
  const start = parseEventStart(ev.start);
  if (ev.all_day) {
    const base = start.toLocaleString(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
      year: "numeric",
    });
    if (!ev.end) return base;
    const end = parseEventStart(ev.end);
    if (end.getTime() - start.getTime() === 86400000) return base;
    return `${base} – ${end.toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric" })}`;
  }
  const base = start.toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
  if (!ev.end) return base;
  const end = parseEventStart(ev.end);
  const sameDay =
    start.getFullYear() === end.getFullYear() &&
    start.getMonth() === end.getMonth() &&
    start.getDate() === end.getDate();
  if (sameDay) {
    return `${base} – ${end.toLocaleString(undefined, { hour: "numeric", minute: "2-digit" })}`;
  }
  return `${base} – ${end.toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`;
}

const STATUS_LABELS: Record<string, string> = {
  scheduled: "Scheduled",
  triggered: "Running",
  in_progress: "In progress",
  done: "Done",
  failed: "Failed",
  missed: "Missed",
  cancelled: "Cancelled",
};

export default function EventDetailModal({
  event,
  calendarPath,
  onEdit,
  onDelete,
  onClose,
  onReload,
  onOpenInChat,
}: Props) {
  const [liveEvent, setLiveEvent] = useState<CalendarEvent>(event);
  const [confirmVariant, setConfirmVariant] = useState<ConfirmVariant>("idle");
  const [error, setError] = useState<string | null>(null);
  const confirmTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  const isRunning = liveEvent.status === "triggered";
  const isDone = liveEvent.status === "done";
  const isFailed = liveEvent.status === "failed";
  const isAgent = liveEvent.assignee === "agent";
  const canFire = isAgent && (liveEvent.status === "scheduled" || liveEvent.status === "missed");
  const showStatusBtn = isAgent && (isRunning || isDone || isFailed);

  // Poll while running (same pattern as kanban).
  useEffect(() => {
    if (!isRunning) return;
    const t = setInterval(() => {
      getVaultCalendar(calendarPath)
        .then((cal) => {
          const updated = cal.events.find((e) => e.id === liveEvent.id);
          if (updated) setLiveEvent(updated);
        })
        .catch(() => {});
    }, 1500);
    return () => clearInterval(t);
  }, [isRunning, calendarPath, liveEvent.id]);

  // Transition catch-up: one extra reload 500ms after running stops.
  const prevRunning = useRef(false);
  useEffect(() => {
    const transitioned = prevRunning.current && !isRunning;
    prevRunning.current = isRunning;
    if (transitioned) {
      const t = setTimeout(onReload, 500);
      return () => clearTimeout(t);
    }
  }, [isRunning, onReload]);

  // Escape to close.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Confirm-pattern helpers (same as kanban).
  const resetVariant = () => {
    if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current);
    setConfirmVariant("idle");
  };
  const showConfirm = (v: ConfirmVariant) => {
    resetVariant();
    setConfirmVariant(v);
    confirmTimerRef.current = setTimeout(() => setConfirmVariant("idle"), 5000);
  };

  const handleFire = async () => {
    try {
      setError(null);
      await fireVaultCalendarEvent(calendarPath, liveEvent.id);
      const cal = await getVaultCalendar(calendarPath);
      const updated = cal.events.find((e) => e.id === liveEvent.id);
      if (updated) setLiveEvent(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fire failed");
    }
  };

  const handleStop = async () => {
    try {
      setError(null);
      await patchVaultCalendarEvent(calendarPath, liveEvent.id, {
        status: "cancelled",
      });
      const cal = await getVaultCalendar(calendarPath);
      const updated = cal.events.find((e) => e.id === liveEvent.id);
      if (updated) setLiveEvent(updated);
      onReload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Stop failed");
    }
  };

  const handleRetry = async () => {
    try {
      setError(null);
      await patchVaultCalendarEvent(calendarPath, liveEvent.id, {
        status: "scheduled",
      });
      await fireVaultCalendarEvent(calendarPath, liveEvent.id);
      const cal = await getVaultCalendar(calendarPath);
      const updated = cal.events.find((e) => e.id === liveEvent.id);
      if (updated) setLiveEvent(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Retry failed");
    }
  };

  const handleStatusClick = () => {
    if (isRunning) {
      if (confirmVariant === "confirm-stop") {
        resetVariant();
        void handleStop();
      } else {
        showConfirm("confirm-stop");
      }
    } else if (isDone || isFailed) {
      if (confirmVariant === "confirm-retry") {
        resetVariant();
        void handleRetry();
      } else {
        showConfirm("confirm-retry");
      }
    }
  };

  const statusIcon = () => {
    if (isRunning) {
      if (confirmVariant === "confirm-stop") {
        return (
          <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor">
            <rect x="3" y="3" width="10" height="10" rx="1" />
          </svg>
        );
      }
      return <span className="cal-detail-spin" />;
    }
    if (isFailed) {
      if (confirmVariant === "confirm-retry") {
        return (
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M2 8a6 6 0 0 1 10.5-4M14 8a6 6 0 0 1-10.5 4" />
            <path d="M12 1v4h4" />
            <path d="M4 15v-4H0" />
          </svg>
        );
      }
      return (
        <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor">
          <path d="M4.646 4.646a.5.5 0 0 1 .708 0L8 7.293l2.646-2.647a.5.5 0 0 1 .708.708L8.707 8l2.647 2.646a.5.5 0 0 1-.708.708L8 8.707l-2.646 2.647a.5.5 0 0 1-.708-.708L7.293 8 4.646 5.354a.5.5 0 0 1 0-.708z" />
        </svg>
      );
    }
    if (isDone) {
      if (confirmVariant === "confirm-retry") {
        return (
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M2 8a6 6 0 0 1 10.5-4M14 8a6 6 0 0 1-10.5 4" />
            <path d="M12 1v4h4" />
            <path d="M4 15v-4H0" />
          </svg>
        );
      }
      return (
        <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor">
          <path d="M13.485 3.929a.5.5 0 0 1 .058.638l-.058.07-7.5 7.5a.5.5 0 0 1-.638.058l-.07-.058-3.5-3.5a.5.5 0 0 1 .638-.765l.07.058L5.5 10.793l7.146-7.147a.5.5 0 0 1 .708 0l.131.11z" />
        </svg>
      );
    }
    return null;
  };

  const statusTitle = () => {
    if (isRunning) {
      return confirmVariant === "confirm-stop" ? "Click to stop" : "Running…";
    }
    if (isDone) {
      return confirmVariant === "confirm-retry" ? "Click to re-run" : "Done";
    }
    if (isFailed) {
      return confirmVariant === "confirm-retry" ? "Click to retry" : "Failed";
    }
    return "";
  };

  const statusClass = () => {
    if (isRunning) {
      return confirmVariant === "confirm-stop"
        ? "cal-detail-status-btn--stop"
        : "cal-detail-status-btn--running";
    }
    if (isDone || isFailed) {
      return confirmVariant === "confirm-retry"
        ? "cal-detail-status-btn--retry"
        : `cal-detail-status-btn--${liveEvent.status}`;
    }
    return "";
  };

  return (
    <div className="cal-modal-backdrop" onClick={onClose}>
      <div className="cal-modal cal-modal--detail" onClick={(e) => e.stopPropagation()}>
        <div className="cal-detail-header">
          <div className="cal-detail-title-row">
            <h3>{liveEvent.title}</h3>
            {showStatusBtn && (
              <button
                className={`cal-detail-status-btn ${statusClass()}`}
                onClick={handleStatusClick}
                title={statusTitle()}
              >
                {statusIcon()}
              </button>
            )}
          </div>
          <div className="cal-detail-badges">
            <span className={`cal-detail-status cal-detail-status--${liveEvent.status}`}>
              {STATUS_LABELS[liveEvent.status] ?? liveEvent.status}
            </span>
            {isAgent && (
              <span className="cal-detail-badge cal-detail-badge--agent">Agent</span>
            )}
            {liveEvent.rrule && (
              <span className="cal-detail-badge cal-detail-badge--recur">Recurring</span>
            )}
          </div>
        </div>

        <div className="cal-detail-meta">
          <span className="cal-detail-date">{formatDate(liveEvent)}</span>
          {liveEvent.all_day && <span className="cal-detail-tag">All day</span>}
        </div>

        <div className="cal-detail-body">
          {liveEvent.body ? (
            <MarkdownView>{liveEvent.body}</MarkdownView>
          ) : (
            <p className="cal-detail-empty">No notes yet. Click Edit to add content.</p>
          )}
        </div>

        {error && (
          <div className="cal-detail-error">{error}</div>
        )}

        <div className="cal-modal-actions">
          <button className="danger" onClick={onDelete}>
            Delete
          </button>
          {canFire && <button onClick={() => void handleFire()}>Fire now</button>}
          {onOpenInChat && (
            <button onClick={() => onOpenInChat(liveEvent)}>Open in chat</button>
          )}
          <div className="spacer" />
          <button onClick={onClose}>Close</button>
          <button className="primary" onClick={() => onEdit(liveEvent)}>
            Edit
          </button>
        </div>
      </div>
    </div>
  );
}
