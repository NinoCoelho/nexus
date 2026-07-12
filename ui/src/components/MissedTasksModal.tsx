/**
 * MissedTasksModal — recovery prompt for calendar events that were flagged
 * ``missed`` (past-due while the computer/server was offline).
 *
 * Lists each missed agent-assigned task with a checkbox. The user picks
 * which to re-run; "Run selected" dispatches each via the fire API. "Later"
 * dismisses the current set so the prompt won't reappear until new missed
 * tasks accumulate.
 */

import { useState } from "react";
import { fireVaultCalendarEvent, type MissedEvent } from "../api/calendar";
import "./MissedTasksModal.css";

interface Props {
  events: MissedEvent[];
  onFired: (eventId: string) => void;
  onDismissAll: () => void;
  onClose: () => void;
}

type RowState = "idle" | "running" | "done" | "error";

function formatWhen(start: string): string {
  if (!start) return "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(start)) {
    const dt = new Date(start + "T00:00:00Z");
    if (Number.isNaN(dt.getTime())) return start;
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

function formatAgo(start: string): string {
  const dt = new Date(start);
  if (Number.isNaN(dt.getTime())) return "";
  const hours = Math.floor((Date.now() - dt.getTime()) / 3_600_000);
  if (hours < 1) {
    const mins = Math.floor((Date.now() - dt.getTime()) / 60_000);
    return mins <= 1 ? "just now" : `${mins}m ago`;
  }
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export default function MissedTasksModal({
  events,
  onFired,
  onDismissAll,
  onClose,
}: Props) {
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(events.map((e) => e.id)),
  );
  const [rowStates, setRowStates] = useState<Record<string, RowState>>({});
  const [globalRunning, setGlobalRunning] = useState(false);

  const runnable = events.filter((e) => e.assignee?.includes("agent"));
  const selectedRunnable = runnable.filter(
    (e) => selected.has(e.id) && rowStates[e.id] !== "done",
  );
  const hasSelection = selectedRunnable.length > 0;

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const runSelected = async () => {
    setGlobalRunning(true);
    await Promise.allSettled(
      selectedRunnable.map(async (ev) => {
        setRowStates((prev) => ({ ...prev, [ev.id]: "running" }));
        try {
          await fireVaultCalendarEvent(ev.path ?? "", ev.id);
          setRowStates((prev) => ({ ...prev, [ev.id]: "done" }));
          onFired(ev.id);
        } catch {
          setRowStates((prev) => ({ ...prev, [ev.id]: "error" }));
        }
      }),
    );
    setGlobalRunning(false);
  };

  const handleBackdrop = () => {
    if (!globalRunning) onClose();
  };

  return (
    <div className="missed-backdrop" onClick={handleBackdrop}>
      <div className="missed-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="missed-header">
          <div className="missed-title">
            Missed tasks
          </div>
          <div className="missed-subtitle">
            {runnable.length} scheduled{" "}
            {runnable.length === 1 ? "task was" : "tasks were"} missed while
            away. Select which to run.
          </div>
        </div>

        <div className="missed-list">
          {runnable.map((ev) => {
            const state = rowStates[ev.id] ?? "idle";
            const checked = selected.has(ev.id);
            return (
              <label
                key={ev.id}
                className={`missed-row missed-row--${state}`}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={state === "running" || state === "done"}
                  onChange={() => toggle(ev.id)}
                />
                <div className="missed-row-body">
                  <div className="missed-row-title">{ev.title}</div>
                  <div className="missed-row-meta">
                    <span>{formatWhen(ev.start)}</span>
                    {formatAgo(ev.start) && (
                      <>
                        <span className="missed-dot">·</span>
                        <span>{formatAgo(ev.start)}</span>
                      </>
                    )}
                    {ev.calendar_title && (
                      <>
                        <span className="missed-dot">·</span>
                        <span>{ev.calendar_title}</span>
                      </>
                    )}
                  </div>
                </div>
                <div className="missed-row-status">
                  {state === "running" && <span className="missed-spinner" />}
                  {state === "done" && <span className="missed-check">&#10003;</span>}
                  {state === "error" && (
                    <span className="missed-error-icon" title="Failed to run">&#10007;</span>
                  )}
                </div>
              </label>
            );
          })}
        </div>

        <div className="missed-actions">
          <button
            className="missed-btn"
            onClick={onDismissAll}
            disabled={globalRunning}
          >
            Later
          </button>
          <button
            className="missed-btn missed-btn--primary"
            onClick={runSelected}
            disabled={!hasSelection || globalRunning}
          >
            {globalRunning
              ? "Running…"
              : hasSelection
                ? `Run ${selectedRunnable.length}`
                : "Select tasks"}
          </button>
        </div>
      </div>
    </div>
  );
}
