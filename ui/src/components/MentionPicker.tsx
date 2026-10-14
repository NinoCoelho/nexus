/**
 * MentionPicker — popover that appears when the user types `@` in the input.
 *
 * Two result groups, bots first: entries from `getBots()` (completing to a
 * plain `@handle` mention that routes the turn to that bot) and vault
 * entries from the server (`/vault/mention`, completing to a
 * `[name](vault://path)` markdown link).
 *
 * Keyboard: ArrowUp/ArrowDown to move, Enter/Tab to select, Escape to close.
 * The parent owns keyboard handling via the imperative `handleKey` ref.
 */

import { forwardRef, useImperativeHandle, useRef, useState, useEffect } from "react";
import type { VaultNode } from "../api";
import type { BotMentionRef } from "./InputBar/useMentionPicker";
import "./MentionPicker.css";

export interface MentionPickerHandle {
  /** Returns true if the key was consumed by the picker. */
  handleKey: (e: React.KeyboardEvent) => boolean;
}

export type MentionItem =
  | { kind: "bot"; bot: BotMentionRef }
  | { kind: "vault"; node: VaultNode };

interface Props {
  results: VaultNode[];
  loading: boolean;
  bots?: BotMentionRef[];
  activeQuery?: string;
  onSelectItem: (item: MentionItem) => void;
  onClose: () => void;
}

const MentionPicker = forwardRef<MentionPickerHandle, Props>(function MentionPicker(
  { results, loading, bots = [], activeQuery = "", onSelectItem, onClose },
  ref,
) {
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  const items: MentionItem[] = [
    ...filterBots(bots, activeQuery).map((bot) => ({ kind: "bot" as const, bot })),
    ...results.map((node) => ({ kind: "vault" as const, node })),
  ];

  useEffect(() => {
    setActive(0);
  }, [activeQuery, results]);

  useEffect(() => {
    listRef.current?.querySelector(`[data-idx="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  useImperativeHandle(ref, () => ({
    handleKey(e) {
      if (items.length === 0) {
        if (e.key === "Escape") { onClose(); return true; }
        return false;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive((i) => (i + 1) % items.length);
        return true;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive((i) => (i - 1 + items.length) % items.length);
        return true;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        onSelectItem(items[active]);
        return true;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return true;
      }
      return false;
    },
  }));

  if (items.length === 0) {
    return (
      <div className="mention-picker">
        <div className="mention-picker-empty">{loading ? "Searching…" : "No matches"}</div>
      </div>
    );
  }

  return (
    <div className="mention-picker" ref={listRef}>
      {items.map((item, i) => {
        if (item.kind === "bot") {
          const b = item.bot;
          return (
            <button
              key={`bot-${b.id}`}
              data-idx={i}
              type="button"
              className={`mention-picker-item${i === active ? " is-active" : ""}`}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => { e.preventDefault(); onSelectItem(item); }}
            >
              <span className="mention-picker-icon mention-picker-icon--bot">
                <svg width="13" height="13" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="4" y="7" width="12" height="9" rx="2" />
                  <path d="M8 7V4.5a2 2 0 0 1 4 0V7" />
                  <circle cx="8" cy="11.5" r="0.6" fill="currentColor" stroke="none" />
                  <circle cx="12" cy="11.5" r="0.6" fill="currentColor" stroke="none" />
                </svg>
              </span>
              <span className="mention-picker-name">{b.name}</span>
              <span className="mention-picker-dir">@{b.handle}</span>
            </button>
          );
        }
        const n = item.node;
        const name = n.path.split("/").pop() || n.path;
        const dir = n.path.includes("/") ? n.path.slice(0, -name.length - 1) : "";
        return (
          <button
            key={`vault-${n.path}`}
            data-idx={i}
            type="button"
            className={`mention-picker-item${i === active ? " is-active" : ""}`}
            onMouseEnter={() => setActive(i)}
            onMouseDown={(e) => { e.preventDefault(); onSelectItem(item); }}
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

function filterBots(bots: BotMentionRef[], query: string): BotMentionRef[] {
  const q = query.trim().toLowerCase();
  if (!q) return bots;
  return bots.filter(
    (b) => b.handle.toLowerCase().startsWith(q) || b.name.toLowerCase().includes(q),
  );
}

export default MentionPicker;
