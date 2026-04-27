/**
 * SlashCommandPicker — popover that appears when the user types `/` at the
 * start of the chat input. Mirrors the @-mention picker pattern: keyboard-
 * navigable list, parent owns key dispatch via the imperative `handleKey`
 * ref, click-to-select also wired.
 */

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import type { SlashCommand } from "../api";
import "./MentionPicker.css";

export interface SlashPickerHandle {
  /** Returns true if the key was consumed by the picker. */
  handleKey: (e: React.KeyboardEvent) => boolean;
}

interface Props {
  results: SlashCommand[];
  onSelect: (cmd: SlashCommand) => void;
  onClose: () => void;
}

const SlashCommandPicker = forwardRef<SlashPickerHandle, Props>(
  function SlashCommandPicker({ results, onSelect, onClose }, ref) {
    const [active, setActive] = useState(0);
    const listRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
      setActive(0);
    }, [results]);

    useEffect(() => {
      listRef.current
        ?.querySelector(`[data-idx="${active}"]`)
        ?.scrollIntoView({ block: "nearest" });
    }, [active]);

    useImperativeHandle(
      ref,
      () => ({
        handleKey(e) {
          if (results.length === 0) {
            if (e.key === "Escape") {
              onClose();
              return true;
            }
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
      }),
      [results, active, onSelect, onClose],
    );

    if (results.length === 0) {
      return (
        <div className="mention-picker">
          <div className="mention-picker-empty">No matching commands</div>
        </div>
      );
    }

    return (
      <div className="mention-picker" ref={listRef}>
        {results.map((c, i) => (
          <button
            key={c.name}
            data-idx={i}
            type="button"
            className={`mention-picker-item slash-picker-item${i === active ? " is-active" : ""}`}
            onMouseEnter={() => setActive(i)}
            onMouseDown={(e) => {
              e.preventDefault();
              onSelect(c);
            }}
          >
            <span className="slash-picker-name">
              /{c.name}
              {c.args_hint ? <span className="slash-picker-args"> {c.args_hint}</span> : null}
            </span>
            <span className="slash-picker-desc">{c.description}</span>
          </button>
        ))}
      </div>
    );
  },
);

export default SlashCommandPicker;
