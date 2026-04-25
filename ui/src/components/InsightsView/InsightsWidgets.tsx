// Shared widget components for InsightsView.

export type Window = 7 | 30 | 90 | 365;

export const WINDOW_LABELS: Record<Window, string> = {
  7: "7 days",
  30: "30 days",
  90: "90 days",
  365: "1 year",
};

export function formatDuration(sec: number): string {
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.round(sec / 60)}m`;
  if (sec < 86400) return `${(sec / 3600).toFixed(1)}h`;
  return `${(sec / 86400).toFixed(1)}d`;
}

export function formatCost(cost: number): string {
  if (cost === 0) return "$0.00";
  if (cost < 0.01) return `$${cost.toFixed(4)}`;
  if (cost < 1) return `$${cost.toFixed(3)}`;
  return `$${cost.toFixed(2)}`;
}

export function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="insights-tile">
      <div className="insights-tile-label">{label}</div>
      <div className="insights-tile-value">{value}</div>
      {hint && <div className="insights-tile-hint">{hint}</div>}
    </div>
  );
}

export function WindowSelect({ days, onChange }: { days: Window; onChange: (d: Window) => void }) {
  const options: Window[] = [7, 30, 90, 365];
  return (
    <div className="insights-window-select">
      {options.map((d) => (
        <button
          key={d}
          className={`insights-window-btn${d === days ? " insights-window-btn--active" : ""}`}
          onClick={() => onChange(d)}
          type="button"
        >
          {WINDOW_LABELS[d]}
        </button>
      ))}
    </div>
  );
}

export function FilterPill({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <div className="insights-filter-pill">
      <span>Filtered by model:</span>
      <strong>{label}</strong>
      <button type="button" className="insights-filter-clear" onClick={onClear} aria-label="Clear filter">
        ✕
      </button>
    </div>
  );
}
