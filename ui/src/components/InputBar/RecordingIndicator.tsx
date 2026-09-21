import { useEffect, useState } from "react";
import { X } from "lucide-react";
import "./RecordingIndicator.css";

interface Props {
  levels: number[];
  seconds: number;
  onCancel: () => void;
  /** Epoch-ms deadline of the "about to send" grace countdown. While it
   *  counts down, resuming speech cancels the send and the recording
   *  continues. Null = normal recording. */
  endingAt?: number | null;
}

function fmt(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default function RecordingIndicator({ levels, seconds, onCancel, endingAt }: Props) {
  const [remainingMs, setRemainingMs] = useState<number | null>(null);

  useEffect(() => {
    if (endingAt == null) {
      setRemainingMs(null);
      return;
    }
    const tickDown = () => setRemainingMs(Math.max(0, endingAt - Date.now()));
    tickDown();
    const id = window.setInterval(tickDown, 100);
    return () => window.clearInterval(id);
  }, [endingAt]);

  const ending = remainingMs != null && remainingMs > 0;
  const progress = ending ? Math.min(1, remainingMs / 1500) : 1;

  return (
    <div className={`recording-indicator${ending ? " recording-indicator--ending" : ""}`} role="status" aria-label="Recording audio">
      <button
        type="button"
        className="recording-cancel"
        onClick={onCancel}
        title="Cancel recording"
        aria-label="Cancel recording"
      >
        <X size={14} />
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
        {ending && (
          <span
            className="recording-ending-bar"
            style={{ width: `${progress * 100}%` }}
            aria-hidden="true"
          />
        )}
      </div>
      {ending ? (
        <span className="recording-hint recording-hint--ending">
          Sending in {Math.ceil(remainingMs! / 1000)}… keep talking to continue
        </span>
      ) : (
        <span className="recording-hint">Release or tap mic to send</span>
      )}
    </div>
  );
}
