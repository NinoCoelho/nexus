import { useEffect, useRef, useState } from "react";
import type { RunningJob } from "../hooks/useRunningJobs";
import "./GlobalSpinner.css";

interface Props {
  jobs: RunningJob[];
  onKill: (jobId: string) => void;
  onGoTo: (sessionId: string | null, type: string) => void;
}

const TYPE_META: Record<string, { icon: string; view: string }> = {
  chat_turn: { icon: "💬", view: "chat" },
  terminal: { icon: "⌨️", view: "chat" },
  subagent: { icon: "🤖", view: "chat" },
  dream: { icon: "💤", view: "dream" },
  heartbeat: { icon: "💓", view: "heartbeat" },
};

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}m${s > 0 ? ` ${s}s` : ""}`;
}

export default function GlobalSpinner({ jobs, onKill, onGoTo }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  if (jobs.length === 0) return null;

  return (
    <div className="gs-container" ref={ref}>
      <button
        className="gs-trigger"
        onClick={() => setOpen((v) => !v)}
        title={`${jobs.length} running task${jobs.length > 1 ? "s" : ""}`}
        type="button"
      >
        <span className="gs-spinner" />
        <span className="gs-count">{jobs.length}</span>
      </button>
      {open && (
        <div className="gs-dropdown">
          <div className="gs-dropdown-header">Running tasks</div>
          {jobs.map((job) => {
            const meta = TYPE_META[job.type] ?? { icon: "⚙️", view: "" };
            const elapsed = job.elapsed_seconds ?? 0;
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
                  <span className="gs-job-elapsed">{formatElapsed(elapsed)}</span>
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
        </div>
      )}
    </div>
  );
}
