import { useRef } from "react";
import "./InputBar.css";

interface Props {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  disabled: boolean;
}

export default function InputBar({ value, onChange, onSend, disabled }: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const adjust = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 144)}px`;
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  };

  return (
    <div className="input-bar">
      <div className="input-bar-stubs">
        <button className="input-stub-btn" disabled title="Coming soon" aria-label="Microphone">
          <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
            <rect x="7" y="2" width="6" height="10" rx="3" />
            <path d="M4 10a6 6 0 0 0 12 0" />
            <line x1="10" y1="16" x2="10" y2="19" />
            <line x1="7" y1="19" x2="13" y2="19" />
          </svg>
        </button>
        <button className="input-stub-btn" disabled title="Coming soon" aria-label="Emoji">
          <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="10" cy="10" r="8" />
            <path d="M7 13s1 2 3 2 3-2 3-2" />
            <circle cx="7.5" cy="8.5" r="1" fill="currentColor" stroke="none" />
            <circle cx="12.5" cy="8.5" r="1" fill="currentColor" stroke="none" />
          </svg>
        </button>
      </div>
      <textarea
        ref={textareaRef}
        className="input-textarea"
        rows={1}
        placeholder="Message Nexus…"
        value={value}
        onChange={(e) => { onChange(e.target.value); adjust(); }}
        onKeyDown={handleKeyDown}
        disabled={disabled}
      />
      <button
        className="input-send-btn"
        onClick={onSend}
        disabled={disabled || !value.trim()}
        aria-label="Send"
      >
        <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
          <line x1="10" y1="17" x2="10" y2="4" />
          <polyline points="4,10 10,4 16,10" />
        </svg>
      </button>
    </div>
  );
}
