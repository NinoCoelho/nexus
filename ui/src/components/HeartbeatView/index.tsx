import { useCallback, useEffect, useRef, useState } from "react";
import {
  listHeartbeats,
  listHeartbeatEvents,
  getHeartbeatLog,
  patchHeartbeat,
  triggerHeartbeat,
  reloadHeartbeats,
  type HeartbeatRun,
  type HeartbeatEvent,
  type FireLogEntry,
} from "../../api/heartbeat";
import { fireVaultCalendarEvent } from "../../api/calendar";
import CardActivityModal from "../CardActivityModal";
import type { KanbanCardStatus } from "../../api";
import "./HeartbeatView.css";

type Tab = "events" | "log";
type StatusVariant = "idle" | "confirm-stop" | "confirm-retry";
type DisplayStatus = "running" | "done" | "failed" | "scheduled" | "missed" | "cancelled";

function displayStatus(raw: string): DisplayStatus {
  if (raw === "triggered") return "running";
  return raw as DisplayStatus;
}

function relTime(iso: string | null): string {
  if (!iso) return "never";
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function shortTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function shortDateTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) +
    " " +
    d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function formatDuration(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function Spinner() {
  return <span className="hb-spin" />;
}

function StatusBadge({
  status,
  variant,
  onClick,
}: {
  status: string;
  variant: StatusVariant;
  onClick: () => void;
}) {
  const badgeClass = () => {
    if (status === "running") {
      return variant === "confirm-stop"
        ? "hb-status-badge hb-status-badge--stop"
        : "hb-status-badge hb-status-badge--running";
    }
    if (status === "failed") {
      return variant === "confirm-retry"
        ? "hb-status-badge hb-status-badge--retry"
        : "hb-status-badge hb-status-badge--failed";
    }
    if (status === "done") {
      return variant === "confirm-retry"
        ? "hb-status-badge hb-status-badge--retry"
        : "hb-status-badge hb-status-badge--done";
    }
    return "hb-status-badge hb-status-badge--play";
  };

  const icon = () => {
    if (status === "running") {
      if (variant === "confirm-stop") {
        return (
          <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor">
            <rect x="3" y="3" width="10" height="10" rx="1" />
          </svg>
        );
      }
      return <Spinner />;
    }
    if (status === "failed") {
      if (variant === "confirm-retry") {
        return (
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="2 8 6 12 14 4" />
            <path d="M14 8A6 6 0 1 1 8 2" />
          </svg>
        );
      }
      return (
        <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <line x1="4" y1="4" x2="12" y2="12" />
          <line x1="12" y1="4" x2="4" y2="12" />
        </svg>
      );
    }
    if (status === "done") {
      if (variant === "confirm-retry") {
        return (
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="2 8 6 12 14 4" />
            <path d="M14 8A6 6 0 1 1 8 2" />
          </svg>
        );
      }
      return (
        <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="3 8 7 12 13 4" />
        </svg>
      );
    }
    return (
      <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor">
        <path d="M5 3l8 5-8 5z" />
      </svg>
    );
  };

  const title = () => {
    if (status === "running") {
      return variant === "confirm-stop"
        ? "Click to cancel"
        : "Running — click to cancel";
    }
    if (status === "failed") {
      return variant === "confirm-retry"
        ? "Click to retry"
        : "Failed — click to retry";
    }
    if (status === "done") {
      return variant === "confirm-retry"
        ? "Click to re-run"
        : "Done — click to re-run";
    }
    return "Fire now";
  };

  return (
    <button
      className={badgeClass()}
      onClick={(e) => { e.stopPropagation(); onClick(); }}
      title={title()}
      type="button"
    >
      {icon()}
    </button>
  );
}

function HeartbeatCard({
  hb,
  onToggle,
  onTrigger,
}: {
  hb: HeartbeatRun;
  onToggle: (id: string, enabled: boolean) => void;
  onTrigger: (id: string) => void;
}) {
  return (
    <div className="hb-card">
      <div className="hb-card-header">
        <span className={`hb-card-health hb-card-health--${hb.health}`} />
        <span className="hb-card-name">{hb.name}</span>
        <span className="hb-card-schedule">{hb.schedule}</span>
        <div className="hb-card-actions">
          <button
            className="hb-btn"
            onClick={() => onToggle(hb.id, !hb.enabled)}
            type="button"
          >
            {hb.enabled ? "Disable" : "Enable"}
          </button>
          <button
            className="hb-btn hb-btn--accent"
            onClick={() => onTrigger(hb.id)}
            type="button"
          >
            Trigger
          </button>
        </div>
      </div>
      <div className="hb-card-desc">{hb.description}</div>
      <div className="hb-card-meta">
        <span>Last check: {relTime(hb.last_check)}</span>
        <span>Last fired: {relTime(hb.last_fired)}</span>
        {hb.next_due && <span>Next due: {hb.next_due === "now" ? "now" : relTime(hb.next_due)}</span>}
      </div>
      {hb.last_error && (
        <div className="hb-card-error">{hb.last_error}</div>
      )}
    </div>
  );
}

function EventRow({
  ev,
  onFire,
  onViewActivity,
  onOpenInChat,
}: {
  ev: HeartbeatEvent;
  onFire: (ev: HeartbeatEvent) => void;
  onViewActivity: (ev: HeartbeatEvent) => void;
  onOpenInChat: (sessionId: string) => void;
}) {
  const [variant, setVariant] = useState<StatusVariant>("idle");
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const resetVariant = useCallback(() => {
    setVariant("idle");
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => {
    resetVariant();
  }, [ev.status, resetVariant]);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const showConfirm = (v: StatusVariant) => {
    resetVariant();
    setVariant(v);
    timerRef.current = setTimeout(() => {
      setVariant("idle");
      timerRef.current = null;
    }, 5000);
  };

  const ds = displayStatus(ev.status);

  const handleStatusClick = () => {
    if (ds === "running") {
      if (variant === "confirm-stop") {
        resetVariant();
      } else {
        showConfirm("confirm-stop");
      }
    } else if (ds === "failed" || ds === "missed") {
      if (variant === "confirm-retry") {
        resetVariant();
        onFire(ev);
      } else {
        showConfirm("confirm-retry");
      }
    } else if (ds === "done") {
      if (variant === "confirm-retry") {
        resetVariant();
        onFire(ev);
      } else {
        showConfirm("confirm-retry");
      }
    } else {
      onFire(ev);
    }
  };

  return (
    <div
      className="hb-event-row"
      onClick={() => {
        if (ev.session_id) onViewActivity(ev);
      }}
    >
      <StatusBadge
        status={ds}
        variant={variant}
        onClick={handleStatusClick}
      />
      <span className="hb-event-title">{ev.title || ev.event_id}</span>
      <span className="hb-event-calendar">{ev.calendar_title}</span>
      <span className="hb-event-time">{shortTime(ev.start)}</span>
      <div className="hb-event-actions">
        {ev.session_id && (
          <button
            className="hb-icon-btn"
            onClick={(e) => {
              e.stopPropagation();
              onOpenInChat(ev.session_id!);
            }}
            title="Open in chat"
            type="button"
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
              <path d="M2 3.5A1.5 1.5 0 0 1 3.5 2h9A1.5 1.5 0 0 1 14 3.5v6A1.5 1.5 0 0 1 12.5 11H6l-3 3V3.5z" />
            </svg>
          </button>
        )}
      </div>
    </div>
  );
}

function LogRow({
  entry,
  onViewActivity,
}: {
  entry: FireLogEntry;
  onViewActivity: (entry: FireLogEntry) => void;
}) {
  return (
    <div
      className="hb-log-row"
      onClick={() => {
        if (entry.session_id) onViewActivity(entry);
      }}
    >
      <StatusBadge
        status={entry.status}
        variant="idle"
        onClick={() => {
          if (entry.session_id) onViewActivity(entry);
        }}
      />
      <span className="hb-log-ts">{shortDateTime(entry.timestamp)}</span>
      <span className="hb-log-title">{entry.event_title || entry.event_id}</span>
      {entry.error ? (
        <span className="hb-log-error">{entry.error}</span>
      ) : (
        <span className="hb-log-duration">{formatDuration(entry.duration_ms)}</span>
      )}
    </div>
  );
}

export default function HeartbeatView({
  onOpenInChat,
  onOpenInVault,
}: {
  onOpenInChat: (sessionId: string) => void;
  onOpenInVault?: (path: string) => void;
}) {
  const [heartbeats, setHeartbeats] = useState<HeartbeatRun[]>([]);
  const [schedulerRunning, setSchedulerRunning] = useState(false);
  const [tickInterval, setTickInterval] = useState<number | null>(null);
  const [events, setEvents] = useState<HeartbeatEvent[]>([]);
  const [logEntries, setLogEntries] = useState<FireLogEntry[]>([]);
  const [tab, setTab] = useState<Tab>("events");
  const [loading, setLoading] = useState(true);

  const [activityEvent, setActivityEvent] = useState<{
    sessionId: string;
    title: string;
    status: KanbanCardStatus;
  } | null>(null);

  const hasRunning = events.some((e) => displayStatus(e.status) === "running");

  const load = useCallback(async () => {
    try {
      const [hbRes, evRes] = await Promise.all([
        listHeartbeats(),
        listHeartbeatEvents(),
      ]);
      setHeartbeats(hbRes.heartbeats);
      setSchedulerRunning(hbRes.scheduler_running);
      setTickInterval(hbRes.tick_interval);
      setEvents(evRes.events);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  const loadLog = useCallback(async () => {
    try {
      if (heartbeats.length === 0) return;
      const hbId = heartbeats[0].id;
      const res = await getHeartbeatLog(hbId, 50);
      setLogEntries(res.entries);
    } catch {
      // ignore
    }
  }, [heartbeats]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (tab === "log") loadLog();
  }, [tab, loadLog]);

  useEffect(() => {
    if (!hasRunning) return;
    const id = setInterval(load, 1500);
    return () => clearInterval(id);
  }, [hasRunning, load]);

  const handleToggle = async (id: string, enabled: boolean) => {
    try {
      await patchHeartbeat(id, enabled);
      await load();
    } catch {
      // ignore
    }
  };

  const handleTrigger = async (id: string) => {
    try {
      await triggerHeartbeat(id);
      await load();
    } catch {
      // ignore
    }
  };

  const handleReload = async () => {
    try {
      await reloadHeartbeats();
      await load();
    } catch {
      // ignore
    }
  };

  const handleFireEvent = async (ev: HeartbeatEvent) => {
    try {
      await fireVaultCalendarEvent(ev.calendar_path, ev.event_id);
      await load();
    } catch {
      // ignore
    }
  };

  const handleViewActivity = (item: HeartbeatEvent | FireLogEntry) => {
    const sessionId = item.session_id;
    if (!sessionId) return;
    const title = "title" in item ? item.title : item.event_title;
    setActivityEvent({
      sessionId,
      title: title || "Event",
      status: (item.status as KanbanCardStatus) || "done",
    });
  };

  if (loading) {
    return (
      <div className="hb-view">
        <div className="hb-empty">Loading…</div>
      </div>
    );
  }

  return (
    <div className="hb-view">
      <div className="hb-view-scroll">
        {/* Scheduler bar */}
        <div className="hb-scheduler-bar">
          <span className={`hb-scheduler-dot ${schedulerRunning ? "hb-scheduler-dot--running" : "hb-scheduler-dot--stopped"}`} />
          <span className="hb-scheduler-label">
            Scheduler <strong>{schedulerRunning ? "Running" : "Stopped"}</strong>
            {tickInterval && ` · tick ${tickInterval / 1000}s`}
          </span>
          <button className="hb-btn" onClick={handleReload} type="button">
            Reload
          </button>
        </div>

        {/* Heartbeat cards */}
        <div className="hb-cards">
          {heartbeats.map((hb) => (
            <HeartbeatCard
              key={hb.id}
              hb={hb}
              onToggle={handleToggle}
              onTrigger={handleTrigger}
            />
          ))}
          {heartbeats.length === 0 && (
            <div className="hb-empty">No heartbeats registered</div>
          )}
        </div>

        {/* Section header with tabs */}
        <div className="hb-section-header">
          <h3>Events</h3>
          <div className="hb-tabs">
            <button
              className={`hb-tab ${tab === "events" ? "hb-tab--active" : ""}`}
              onClick={() => setTab("events")}
              type="button"
            >
              Scheduled ({events.length})
            </button>
            <button
              className={`hb-tab ${tab === "log" ? "hb-tab--active" : ""}`}
              onClick={() => setTab("log")}
              type="button"
            >
              Fire Log ({logEntries.length})
            </button>
          </div>
        </div>

        {/* Tab content */}
        {tab === "events" && (
          <div className="hb-event-list">
            {events.length === 0 ? (
              <div className="hb-empty">No agent-assigned calendar events</div>
            ) : (
              events.map((ev) => (
                <EventRow
                  key={ev.event_id}
                  ev={ev}
                  onFire={handleFireEvent}
                  onViewActivity={handleViewActivity}
                  onOpenInChat={onOpenInChat}
                />
              ))
            )}
          </div>
        )}

        {tab === "log" && (
          <div className="hb-event-list">
            {logEntries.length === 0 ? (
              <div className="hb-empty">No fire log entries yet</div>
            ) : (
              logEntries.map((entry) => (
                <LogRow
                  key={entry.id}
                  entry={entry}
                  onViewActivity={handleViewActivity}
                />
              ))
            )}
          </div>
        )}
      </div>

      {/* Activity modal (reuses kanban's CardActivityModal) */}
      {activityEvent && (
        <CardActivityModal
          sessionId={activityEvent.sessionId}
          cardTitle={activityEvent.title}
          status={activityEvent.status}
          onClose={() => setActivityEvent(null)}
          onOpenInVault={onOpenInVault}
        />
      )}
    </div>
  );
}
