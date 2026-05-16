import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import "./MentionPicker.css";

export interface SecretPickerHandle {
  handleKey: (e: React.KeyboardEvent) => boolean;
}

interface Props {
  results: string[];
  onSelect: (name: string) => void;
  onClose: () => void;
}

const SecretPicker = forwardRef<SecretPickerHandle, Props>(
  function SecretPicker({ results, onSelect, onClose }, ref) {
    const [active, setActive] = useState(0);
    const listRef = useRef<HTMLDivElement>(null);

    useEffect(() => { setActive(0); }, [results]);

    useEffect(() => {
      listRef.current
        ?.querySelector(`[data-idx="${active}"]`)
        ?.scrollIntoView({ block: "nearest" });
    }, [active]);

    useImperativeHandle(ref, () => ({
      handleKey(e) {
        if (results.length === 0) {
          if (e.key === "Escape") { onClose(); return true; }
          return false;
        }
        if (e.key === "ArrowDown") {
          e.preventDefault();
          setActive((i) => (i + 1) % results.length);
          return true;
        }
        if (e.key === "ArrowUp") {
          e.preventDefault();
          setActive((i) => (i - 1 + results.length) % results.length);
          return true;
        }
        if (e.key === "Enter" || e.key === "Tab") {
          e.preventDefault();
          onSelect(results[active]);
          return true;
        }
        if (e.key === "Escape") {
          e.preventDefault();
          onClose();
          return true;
        }
        return false;
      },
    }), [results, active, onSelect, onClose]);

    if (results.length === 0) {
      return (
        <div className="mention-picker">
          <div className="mention-picker-empty">No credentials found</div>
        </div>
      );
    }

    return (
      <div className="mention-picker" ref={listRef}>
        {results.map((name, i) => (
          <button
            key={name}
            data-idx={i}
            type="button"
            className={`mention-picker-item secret-picker-item${i === active ? " is-active" : ""}`}
            onMouseEnter={() => setActive(i)}
            onMouseDown={(e) => { e.preventDefault(); onSelect(name); }}
          >
            <span className="mention-picker-icon">
              <svg width="13" height="13" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                <path d="M8.5 11.5a2.5 2.5 0 1 0 3 0M5 8V6a5 5 0 0 1 10 0v2a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2h10" />
              </svg>
            </span>
            <span className="secret-picker-name">${name}</span>
          </button>
        ))}
      </div>
    );
  },
);

export default SecretPicker;
