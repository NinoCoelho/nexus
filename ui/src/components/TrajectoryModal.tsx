import { useEffect, useState } from "react";
import { getSessionTrajectories, type TrajectoryRecord } from "../api";
import "./TrajectoryModal.css";

interface Props {
  sessionId: string | null;
  open: boolean;
  onClose: () => void;
}

function fmtTs(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
}

function JsonBlock({ value }: { value: unknown }) {
  return <pre className="trajectory-json">{JSON.stringify(value, null, 2)}</pre>;
}

function RecordCard({ rec }: { rec: TrajectoryRecord }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="trajectory-record">
      <button
        type="button"
        className="trajectory-record-head"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="trajectory-record-title">
          Turn {rec.turn_index} · {fmtTs(rec.timestamp)}
        </span>
        <span className="trajectory-record-id">{rec.trajectory_id.slice(0, 8)}</span>
        <span className={`trajectory-caret${open ? " is-open" : ""}`}>▸</span>
      </button>
      {open && (
        <div className="trajectory-record-body">
          <h4>State</h4>
          <JsonBlock value={rec.state} />
          <h4>Action</h4>
          <JsonBlock value={rec.action} />
          <h4>Reward</h4>
          <JsonBlock value={rec.reward} />
        </div>
      )}
    </div>
  );
}

export default function TrajectoryModal({ sessionId, open, onClose }: Props) {
  const [loading, setLoading] = useState(false);
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [records, setRecords] = useState<TrajectoryRecord[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !sessionId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    getSessionTrajectories(sessionId)
      .then((res) => {
        if (cancelled) return;
        setEnabled(res.enabled);
        setRecords(res.records);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [open, sessionId]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="trajectory-modal-backdrop" onClick={onClose} role="presentation">
      <div
        className="trajectory-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Trajectory records"
      >
        <div className="trajectory-modal-header">
          <h2>Trajectory log</h2>
          <button className="trajectory-modal-close" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="trajectory-modal-body">
          {loading && <p className="trajectory-empty">Loading…</p>}
          {error && <p className="trajectory-empty">Failed to load: {error}</p>}
          {!loading && !error && enabled === false && (
            <p className="trajectory-empty">
              Trajectory logging is disabled. Set <code>NEXUS_TRAJECTORIES=1</code>{" "}
              and restart the server to capture future turns.
            </p>
          )}
          {!loading && !error && enabled && records.length === 0 && (
            <p className="trajectory-empty">
              No records yet for this session — try a new turn.
            </p>
          )}
          {!loading && !error && records.map((r) => (
            <RecordCard key={r.trajectory_id} rec={r} />
          ))}
        </div>
      </div>
    </div>
  );
}
