/**
 * MentionPicker — popover that appears when the user types `@` in the input.
 *
 * Receives pre-ranked vault entries from the server (`/vault/mention`) and
 * renders them. On selection the parent inserts a `[name](vault://path)`
 * markdown link.
 *
 * Keyboard: ArrowUp/ArrowDown to move, Enter/Tab to select, Escape to close.
 * The parent owns keyboard handling via the imperative `handleKey` ref.
 */

import { forwardRef, useImperativeHandle, useRef, useState, useEffect } from "react";
import type { VaultNode } from "../api";
import "./MentionPicker.css";

export interface MentionPickerHandle {
  /** Returns true if the key was consumed by the picker. */
  handleKey: (e: React.KeyboardEvent) => boolean;
}

interface Props {
  results: VaultNode[];
  loading: boolean;
  onSelect: (node: VaultNode) => void;
  onClose: () => void;
}

const MentionPicker = forwardRef<MentionPickerHandle, Props>(function MentionPicker(
  { results, loading, onSelect, onClose },
  ref,
) {
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setActive(0);
  }, [results]);

  useEffect(() => {
    listRef.current?.querySelector(`[data-idx="${active}"]`)?.scrollIntoView({ block: "nearest" });
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
        <div className="mention-picker-empty">{loading ? "Searching…" : "No vault matches"}</div>
      </div>
    );
  }

  return (
    <div className="mention-picker" ref={listRef}>
      {results.map((n, i) => {
        const name = n.path.split("/").pop() || n.path;
        const dir = n.path.includes("/") ? n.path.slice(0, -name.length - 1) : "";
        return (
          <button
            key={n.path}
            data-idx={i}
            type="button"
            className={`mention-picker-item${i === active ? " is-active" : ""}`}
            onMouseEnter={() => setActive(i)}
            onMouseDown={(e) => { e.preventDefault(); onSelect(n); }}
          >
            <span className="mention-picker-icon">
              {n.type === "dir" ? (
                <svg width="13" height="13" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M2 6a1 1 0 0 1 1-1h4l2 2h8a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V6z" />
                </svg>
              ) : (
                <svg width="13" height="13" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M4 2h8l4 4v12a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z" />
                  <polyline points="12 2 12 6 16 6" />
                </svg>
              )}
            </span>
            <span className="mention-picker-name">{name}</span>
            {dir && <span className="mention-picker-dir">{dir}</span>}
          </button>
        );
      })}
    </div>
  );
});

export default MentionPicker;
