import { useEffect, useRef, useState } from "react";
import type { HitlEventRow, HitlEventStatus } from "../api";
import type { PushPermission } from "../hooks/usePushSubscription";
import "./NotificationBell.css";

interface Props {
  history: HitlEventRow[];
  pendingCount: number;
  pushPermission: PushPermission;
  pushSubscribed: boolean;
  onRequestPushPermission: () => void;
  onRefresh: () => void;
}

export default function NotificationBell({
  history,
  pendingCount,
  pushPermission,
  pushSubscribed,
  onRequestPushPermission,
  onRefresh,
}: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    onRefresh();
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onClick);
      window.removeEventListener("keydown", onKey);
    };
  }, [open, onRefresh]);

  const showEnableBanner =
    pushPermission === "default" || (pushPermission === "granted" && !pushSubscribed);

  return (
    <div className="nx-bell-wrap" ref={ref}>
      <button
        type="button"
        className="header-btn nx-bell-btn"
        onClick={() => setOpen((v) => !v)}
        title={pendingCount > 0 ? `${pendingCount} pending` : "Notifications"}
        aria-label="Notifications"
      >
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
          <path d="M4.5 8.5a5.5 5.5 0 0 1 11 0c0 4 1.5 5.5 1.5 5.5h-14s1.5-1.5 1.5-5.5z" />
          <path d="M8 17a2 2 0 0 0 4 0" />
        </svg>
        {pendingCount > 0 && (
          <span className="nx-bell-badge" aria-hidden>
            {pendingCount > 9 ? "9+" : pendingCount}
          </span>
        )}
      </button>

      {open && (
        <div className="nx-bell-panel" role="dialog" aria-label="Notifications">
          <div className="nx-bell-head">
            <span>Notifications</span>
            {pendingCount > 0 && (
              <span className="nx-bell-pending-pill">
                {pendingCount} pending
              </span>
            )}
          </div>

          {showEnableBanner && (
            <button
              type="button"
              className="nx-bell-enable"
              onClick={onRequestPushPermission}
            >
              Enable browser notifications →
            </button>
          )}
          {pushPermission === "denied" && (
            <div className="nx-bell-note">
              Notifications blocked — enable them in your browser's site settings to receive prompts when no Nexus tab is open.
            </div>
          )}

          {history.length === 0 ? (
            <div className="nx-bell-empty">No HITL events yet.</div>
          ) : (
            <ul className="nx-bell-list">
              {history.map((row) => (
                <li key={row.request_id} className={`nx-bell-item nx-bell-item--${row.status}`}>
                  <div className="nx-bell-item-row">
                    <span className={`nx-bell-status nx-bell-status--${row.status}`}>
                      {statusLabel(row.status)}
                    </span>
                    <span className="nx-bell-time" title={row.created_at}>
                      {relativeTime(row.created_at)}
                    </span>
                  </div>
                  <div className="nx-bell-prompt">{row.prompt}</div>
                  {row.answer && (
                    <div className="nx-bell-answer">→ {row.answer}</div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function statusLabel(s: HitlEventStatus): string {
  switch (s) {
    case "pending": return "Waiting";
    case "answered": return "Answered";
    case "auto_answered": return "Auto";
    case "cancelled": return "Cancelled";
    case "timed_out": return "Timed out";
  }
}

function relativeTime(iso: string): string {
  // SQLite created_at is in UTC ("YYYY-MM-DD HH:MM:SS") but lacks a timezone
  // marker — the string is parsed as local time by Date(). Append "Z" so
  // the diff is correct.
  const utcMs = Date.parse(iso.includes("T") ? iso : iso.replace(" ", "T") + "Z");
  if (Number.isNaN(utcMs)) return iso;
  const seconds = Math.max(0, Math.floor((Date.now() - utcMs) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  const d = Math.floor(h / 24);
  return `${d}d`;
}
