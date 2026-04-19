import "./ContextBar.css";

interface Props {
  value: string;
  onChange: (v: string) => void;
  onDismiss: () => void;
  disabled: boolean;
}

export default function ContextBar({ value, onChange, onDismiss, disabled }: Props) {
  return (
    <div className="context-bar">
      <span className="context-bar-label">Optional: add context for this session</span>
      <input
        className="context-bar-input"
        type="text"
        placeholder="e.g. target: prod-database"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
      />
      <button
        className="context-bar-dismiss"
        onClick={onDismiss}
        aria-label="Dismiss"
        disabled={disabled}
      >
        ×
      </button>
    </div>
  );
}
