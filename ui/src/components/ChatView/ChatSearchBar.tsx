import { useEffect, useMemo, useRef, useState } from "react";
import type { Message } from "./index";
import "./ChatSearchBar.css";

interface Props {
  open: boolean;
  messages: Message[];
  onClose: () => void;
  /** Called when user navigates to a match — receives the visible message index. */
  onJumpTo: (msgIndex: number) => void;
}

export interface MatchInfo {
  msgIndex: number;
}

function findMatches(messages: Message[], query: string): MatchInfo[] {
  if (!query.trim()) return [];
  const q = query.toLowerCase();
  const matches: MatchInfo[] = [];
  messages.forEach((m, idx) => {
    const text = (m.content ?? "").toLowerCase();
    if (text.includes(q)) matches.push({ msgIndex: idx });
  });
  return matches;
}

export default function ChatSearchBar({ open, messages, onClose, onJumpTo }: Props) {
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const matches = useMemo(() => findMatches(messages, query), [messages, query]);

  useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 30);
    } else {
      setQuery("");
      setCursor(0);
    }
  }, [open]);

  useEffect(() => {
    setCursor(0);
  }, [query]);

  useEffect(() => {
    if (matches.length > 0 && cursor < matches.length) {
      onJumpTo(matches[cursor].msgIndex);
    }
  }, [cursor, matches, onJumpTo]);

  if (!open) return null;

  const next = () => {
    if (matches.length === 0) return;
    setCursor((c) => (c + 1) % matches.length);
  };
  const prev = () => {
    if (matches.length === 0) return;
    setCursor((c) => (c - 1 + matches.length) % matches.length);
  };

  return (
    <div className="chat-search-bar" role="search">
      <input
        ref={inputRef}
        type="search"
        className="chat-search-input"
        placeholder="Find in chat…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            if (e.shiftKey) prev(); else next();
          } else if (e.key === "Escape") {
            e.preventDefault();
            onClose();
          }
        }}
        aria-label="Search in current chat"
      />
      <span className="chat-search-count">
        {query.trim()
          ? matches.length === 0
            ? "0"
            : `${cursor + 1}/${matches.length}`
          : ""}
      </span>
      <button
        type="button"
        className="chat-search-btn"
        onClick={prev}
        disabled={matches.length === 0}
        aria-label="Previous match"
        title="Previous (Shift+Enter)"
      >
        ↑
      </button>
      <button
        type="button"
        className="chat-search-btn"
        onClick={next}
        disabled={matches.length === 0}
        aria-label="Next match"
        title="Next (Enter)"
      >
        ↓
      </button>
      <button
        type="button"
        className="chat-search-btn chat-search-close"
        onClick={onClose}
        aria-label="Close search"
        title="Close (Esc)"
      >
        ×
      </button>
    </div>
  );
}
