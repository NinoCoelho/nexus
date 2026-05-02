import "./RecordingIndicator.css";

interface Props {
  levels: number[];
  seconds: number;
  onCancel: () => void;
}

function fmt(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default function RecordingIndicator({ levels, seconds, onCancel }: Props) {
  return (
    <div className="recording-indicator" role="status" aria-label="Recording audio">
      <button
        type="button"
        className="recording-cancel"
        onClick={onCancel}
        title="Cancel recording"
        aria-label="Cancel recording"
      >
        ×
      </button>
      <span className="recording-dot" aria-hidden="true" />
      <span className="recording-time">{fmt(seconds)}</span>
      <div className="recording-waveform" aria-hidden="true">
        {levels.map((v, i) => (
          <span
            key={i}
            className="recording-bar"
            style={{ height: `${Math.max(8, Math.min(100, v * 100))}%` }}
          />
        ))}
      </div>
      <span className="recording-hint">Release or tap mic to send</span>
    </div>
  );
}
