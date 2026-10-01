/**
 * CalendarListPanel — sidebar panel listing vault calendars (like
 * SessionsPanel for chats / KanbanListPanel for boards). Selection is
 * lifted to App so the main-area CalendarView and this list stay in sync.
 * Creating calendars stays in the Calendar header ("Add new").
 */

import { useCallback, useEffect, useState } from "react";
import { listVaultCalendars, type CalendarSummary } from "../../api/calendar";

interface Props {
  selectedPath: string | null;
  onSelect: (path: string) => void;
  /** Bumped when a calendar is created/deleted elsewhere so the list refreshes. */
  refreshKey?: number;
}

export default function CalendarListPanel({ selectedPath, onSelect, refreshKey = 0 }: Props) {
  const [calendars, setCalendars] = useState<CalendarSummary[] | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const res = await listVaultCalendars();
      setCalendars(res.calendars);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh, revision, refreshKey]);

  return (
    <div className="sidebar-section sidebar-calendar-section">
      <div className="sidebar-sessions-header">
        <div className="sidebar-section-label">
          Calendars{calendars ? ` · ${calendars.length}` : ""}
        </div>
        <button
          className="sidebar-collapse-all-btn"
          onClick={() => setRevision((n) => n + 1)}
          disabled={loading}
          title="Reload calendars"
          aria-label="Reload calendars"
        >
          <svg width="12" height="12" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="3 4 3 9 8 9" />
            <polyline points="17 16 17 11 12 11" />
            <path d="M5 9a6 6 0 0 1 10-2.5L17 9" />
            <path d="M15 11a6 6 0 0 1-10 2.5L3 11" />
          </svg>
          Refresh
        </button>
      </div>
      {error && <div className="sidebar-error">Couldn&apos;t load calendars.</div>}
      <div className="sidebar-project-list">
        {(calendars ?? []).map((c) => (
          <button
            key={c.path}
            className={`sidebar-project-row${selectedPath === c.path ? " sidebar-project-row--active" : ""}`}
            onClick={() => onSelect(c.path)}
            title={c.path}
          >
            <span className="sidebar-project-row-name">{c.title}</span>
            <span className="sidebar-project-count">{c.event_count}</span>
          </button>
        ))}
        {calendars !== null && calendars.length === 0 && !error && (
          <div className="sidebar-project-empty">
            No calendars yet — use "Add new" in the calendar toolbar.
          </div>
        )}
      </div>
    </div>
  );
}
