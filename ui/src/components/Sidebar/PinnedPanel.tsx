import { useEffect, useState } from "react";
import { listPins, type PinnedMessage } from "../../api";
import "./PinnedPanel.css";

interface Props {
  /** Bumped externally when something pin-related changed so the panel refetches. */
  refreshKey?: number;
  onOpenSession: (sessionId: string) => void;
}

export default function PinnedPanel({ refreshKey, onOpenSession }: Props) {
  const [pins, setPins] = useState<PinnedMessage[]>([]);
  const [open, setOpen] = useState(true);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listPins(20)
      .then((p) => { if (!cancelled) setPins(p); })
      .catch(() => { if (!cancelled) setPins([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [refreshKey]);

  if (!loading && pins.length === 0) return null;

  return (
    <div className="sidebar-section sidebar-pinned-section">
      <button
        type="button"
        className="sidebar-section-label sidebar-pinned-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span>Pinned ({pins.length})</span>
        <span className={`sidebar-pinned-caret${open ? " is-open" : ""}`}>▸</span>
      </button>
      {open && (
        <div className="sidebar-pinned-list">
          {pins.map((p) => (
            <button
              key={`${p.session_id}-${p.seq}`}
              type="button"
              className="sidebar-pinned-item"
              onClick={() => onOpenSession(p.session_id)}
              title={`${p.session_title} · ${p.role}`}
            >
              <span className="sidebar-pinned-title">{p.session_title}</span>
              <span className="sidebar-pinned-snippet">{p.content.slice(0, 80)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
