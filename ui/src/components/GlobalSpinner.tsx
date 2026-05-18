import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { RunningJob } from "../hooks/useRunningJobs";
import "./GlobalSpinner.css";

interface Props {
  jobs: RunningJob[];
  onKill: (jobId: string) => void;
  onGoTo: (sessionId: string | null, type: string) => void;
}

const TYPE_META: Record<string, { icon: string; view: string }> = {
  chat_turn: { icon: "\u{1F4AC}", view: "chat" },
  terminal: { icon: "\u2328\uFE0F", view: "chat" },
  subagent: { icon: "\u{1F916}", view: "chat" },
  dream: { icon: "\u{1F4A4}", view: "dream" },
  heartbeat: { icon: "\u{1F493}", view: "heartbeat" },
  calendar: { icon: "\u{1F4C5}", view: "calendar" },
};

function formatElapsed(seconds: number): string {
  if (seconds < 1) return "";
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}m${s > 0 ? ` ${s}s` : ""}`;
}

export default function GlobalSpinner({ jobs, onKill, onGoTo }: Props) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLDivElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const [tick, setTick] = useState(0);
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null);

  useEffect(() => {
    if (jobs.length === 0) return;
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [jobs.length]);

  useLayoutEffect(() => {
    if (!open || !triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    setPos({ top: rect.bottom + 6, right: window.innerWidth - rect.right });
    return () => setPos(null);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      const target = e.target as Node;
      if (triggerRef.current?.contains(target)) return;
      if (dropdownRef.current?.contains(target)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  if (jobs.length === 0) return null;

  const now = Date.now() / 1000;

  return (
    <div className="gs-container" ref={triggerRef}>
      <button
        className="gs-trigger"
        onClick={() => setOpen((v) => !v)}
        title={`${jobs.length} running task${jobs.length > 1 ? "s" : ""}`}
        type="button"
      >
        <span className="gs-spinner" />
        <span className="gs-count">{jobs.length}</span>
      </button>
      {open && pos && createPortal(
        <div
          className="gs-dropdown gs-dropdown--portal"
          ref={dropdownRef}
          style={{ top: pos.top, right: pos.right }}
        >
          <div className="gs-dropdown-header">Running tasks</div>
          {jobs.map((job) => {
            const meta = TYPE_META[job.type] ?? { icon: "\u2699\uFE0F", view: "" };
            const elapsed = Math.max(0, now - job.started_at + tick * 0);
            const elapsedStr = formatElapsed(elapsed);
            return (
              <div key={job.id} className="gs-job-row">
                <button
                  className="gs-job-label"
                  type="button"
                  onClick={() => {
                    onGoTo(job.session_id, job.type);
                    setOpen(false);
                  }}
                  title="Go to"
                >
                  <span className="gs-job-icon">{meta.icon}</span>
                  <span className="gs-job-text">{job.label}</span>
                  {elapsedStr && <span className="gs-job-elapsed">{elapsedStr}</span>}
                </button>
                <button
                  className="gs-job-kill"
                  type="button"
                  onClick={() => onKill(job.id)}
                  title="Kill"
                >
                  &times;
                </button>
              </div>
            );
          })}
        </div>,
        document.body,
      )}
    </div>
  );
}
